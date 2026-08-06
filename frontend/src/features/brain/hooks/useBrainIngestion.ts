import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";

import { latticeApi } from "@/api/client";
import { triggerBrainRecall, type BrainState } from "@/components/LivingBrain";
import { t, type Language } from "@/i18n";
import { browserFolderNameFromFiles, pickFolder as pickFolderSelection } from "@/lib/folderPicker";
import { buildBrainReadiness, extractIngestionEvidence, parseKnowledgeGraph } from "../brainData";
import type {
  BrainReadiness,
  EmergenceEvent,
  IngestionEvidence,
  IngestionPipelineStage,
  IngestionSourceType,
  IngestionState,
  KnowledgeGraphModel,
  MemoryFragment,
} from "../types";

const EMPTY_INGESTION_STATES: Record<IngestionSourceType, IngestionState | null> = {
  chat: null,
  file: null,
  folder: null,
  note: null,
  web: null,
};

type IngestionBaseline = {
  memories: number;
  entities: number;
  label: string;
  memoryKnown: boolean;
  graphKnown: boolean;
  nodeIds: Set<string>;
};

export function useBrainIngestion({
  language,
  brainReadiness,
  memoryFragments,
  graphModel,
  memoriesFetched,
  graphFetched,
  requestDetails,
  setLastRecallQuery,
  setMemoryFeedback,
  onBrainChange,
}: {
  language: Language;
  brainReadiness: BrainReadiness;
  memoryFragments: MemoryFragment[];
  graphModel: KnowledgeGraphModel;
  memoriesFetched: boolean;
  graphFetched: boolean;
  requestDetails: () => void;
  setLastRecallQuery: React.Dispatch<React.SetStateAction<string>>;
  setMemoryFeedback: React.Dispatch<React.SetStateAction<string | null>>;
  onBrainChange: (state: BrainState, intensity?: number) => void;
}) {
  const qc = useQueryClient();
  const [uploadingDocument, setUploadingDocument] = React.useState(false);
  const [ingestionStates, setIngestionStates] = React.useState<Record<IngestionSourceType, IngestionState | null>>(
    EMPTY_INGESTION_STATES,
  );
  const [emergenceEvents, setEmergenceEvents] = React.useState<EmergenceEvent[]>([]);
  const [synthesisActive, setSynthesisActive] = React.useState(false);
  const pendingBaselineRef = React.useRef<Partial<Record<IngestionSourceType, IngestionBaseline>>>({});

  React.useEffect(() => {
    if (!emergenceEvents[0]?.id) return;
    setSynthesisActive(true);
    const timer = window.setTimeout(() => setSynthesisActive(false), 1650);
    return () => window.clearTimeout(timer);
  }, [emergenceEvents]);

  // `stage` is always a mid-flight stage here ("indexing" from completeIngestion);
  // terminal stages are written by resolveEmergence/failIngestion below, which
  // stamp completedAt themselves.
  const setStage = React.useCallback((sourceType: IngestionSourceType, stage: IngestionPipelineStage) => {
    setIngestionStates((previous) => {
      const current = previous[sourceType];
      if (!current) return previous;
      return {
        ...previous,
        [sourceType]: { ...current, stage },
      };
    });
  }, []);

  const beginIngestion = React.useCallback((sourceType: IngestionSourceType, label: string) => {
    requestDetails();
    pendingBaselineRef.current[sourceType] = {
      memories: Math.max(brainReadiness.signals.memoryCount, memoryFragments.length),
      entities: Math.max(brainReadiness.signals.conceptCount, graphModel.nodes.length),
      label,
      memoryKnown: memoriesFetched,
      graphKnown: graphFetched,
      nodeIds: new Set(graphModel.nodes.map((node) => node.id)),
    };
    setIngestionStates((previous) => ({
      ...previous,
      [sourceType]: {
        sourceType,
        label,
        stage: "preparing",
        startedAt: Date.now(),
        completedAt: null,
        newMemories: 0,
        newEntities: 0,
        nodeIds: [],
      },
    }));
  }, [brainReadiness.signals.conceptCount, brainReadiness.signals.memoryCount, graphFetched, graphModel.nodes, memoriesFetched, memoryFragments.length, requestDetails]);

  const resolveEmergence = React.useCallback((
    sourceType: IngestionSourceType,
    memoryCount: number,
    entityCount: number,
    nextGraph: KnowledgeGraphModel,
    evidence: IngestionEvidence,
  ) => {
    const baseline = pendingBaselineRef.current[sourceType];
    const label = baseline?.label ?? "";
    const graphDiff = baseline?.graphKnown
      ? nextGraph.nodes.filter((node) => !baseline.nodeIds.has(node.id)).map((node) => node.id)
      : [];
    const evidencedNodes = evidence.nodeIds.filter((nodeId) => !baseline?.graphKnown || !baseline.nodeIds.has(nodeId));
    const nodeIds = Array.from(new Set([...graphDiff, ...evidencedNodes])).slice(0, 24);
    const newMemories = baseline?.memoryKnown ? Math.max(0, memoryCount - baseline.memories) : 0;
    const newEntities = baseline?.graphKnown
      ? Math.max(nodeIds.length, Math.max(0, entityCount - baseline.entities))
      : nodeIds.length;
    delete pendingBaselineRef.current[sourceType];
    setIngestionStates((previous) => {
      const current = previous[sourceType];
      if (!current) return previous;
      return {
        ...previous,
        [sourceType]: {
          ...current,
          stage: "complete",
          completedAt: Date.now(),
          newMemories,
          newEntities,
          nodeIds,
          chunkCount: evidence.chunkCount,
          duplicate: evidence.duplicate,
          provenanceId: evidence.provenanceId,
          extraction: evidence.extraction,
        },
      };
    });
    setEmergenceEvents((events) => [{
      id: `${sourceType}-${Date.now()}`,
      sourceType,
      label,
      newMemories,
      newEntities,
      nodeIds,
      at: Date.now(),
    }, ...events].slice(0, 10));
  }, []);

  const completeIngestion = React.useCallback(async (sourceType: IngestionSourceType, data: unknown = null) => {
    setStage(sourceType, "indexing");
    const evidence = extractIngestionEvidence(data);
    const [managerResult, graphResult] = await Promise.all([
      latticeApi.memoryManager(),
      latticeApi.graphPreview(48),
    ]);
    const nextGraph = graphResult.ok ? parseKnowledgeGraph(graphResult.data) : graphModel;
    const nextReadiness = managerResult.ok
      ? buildBrainReadiness(managerResult.data, memoryFragments.length, nextGraph.nodes.length)
      : brainReadiness;
    if (managerResult.ok) qc.setQueryData(["memoryManager"], managerResult);
    if (graphResult.ok) qc.setQueryData(["graphPreview"], graphResult);
    resolveEmergence(
      sourceType,
      Math.max(nextReadiness.signals.memoryCount, memoryFragments.length),
      Math.max(nextReadiness.signals.conceptCount, nextGraph.nodes.length),
      nextGraph,
      evidence,
    );
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["chatHistory"] }),
      qc.invalidateQueries({ queryKey: ["graph"] }),
      qc.invalidateQueries({ queryKey: ["memoryBrainProof"] }),
      qc.invalidateQueries({ queryKey: ["memoryBrainBrief"] }),
      // Freshness + background jobs may have moved after an ingest completes.
      qc.invalidateQueries({ queryKey: ["vectorFreshness"] }),
      qc.invalidateQueries({ queryKey: ["ingestionJobs"] }),
    ]);
    return {
      memories: Math.max(nextReadiness.signals.memoryCount, memoryFragments.length),
      entities: Math.max(nextReadiness.signals.conceptCount, nextGraph.nodes.length),
    };
  }, [brainReadiness, graphModel, memoryFragments.length, qc, resolveEmergence, setStage]);

  const failIngestion = React.useCallback((sourceType: IngestionSourceType, reason: string) => {
    delete pendingBaselineRef.current[sourceType];
    setIngestionStates((previous) => {
      const current = previous[sourceType];
      if (!current) return previous;
      return {
        ...previous,
        [sourceType]: { ...current, stage: "error", completedAt: Date.now(), error: reason },
      };
    });
  }, []);

  const uploadDocument = React.useCallback(async (file: File) => {
    if (uploadingDocument) return;
    setUploadingDocument(true);
    setMemoryFeedback(t(language, "brain.upload.pending", { name: file.name }));
    onBrainChange("recalling", 0.86);
    beginIngestion("file", file.name);
    try {
      const result = await latticeApi.uploadDocument(file);
      if (!result.ok) {
        const reason = result.error || "unavailable";
        setMemoryFeedback(t(language, "brain.upload.failed", { reason }));
        failIngestion("file", reason);
        return;
      }
      setMemoryFeedback(t(language, "brain.upload.saved", { name: file.name }));
      setLastRecallQuery(file.name);
      await completeIngestion("file", result.data);
      triggerBrainRecall();
    } finally {
      setUploadingDocument(false);
    }
  }, [beginIngestion, completeIngestion, failIngestion, language, onBrainChange, setLastRecallQuery, setMemoryFeedback, uploadingDocument]);

  const connectFolder = React.useCallback(async (path: string) => {
    const target = path.trim();
    if (!target) return;
    setMemoryFeedback(t(language, "brain.ingest.folder.pending", { path: target }));
    onBrainChange("recalling", 0.84);
    beginIngestion("folder", target);
    const result = await latticeApi.connectFolder(target);
    if (!result.ok) {
      const reason = result.error || "unavailable";
      setMemoryFeedback(t(language, "brain.ingest.folder.failed", { reason }));
      failIngestion("folder", reason);
      return;
    }
    setMemoryFeedback(t(language, "brain.ingest.folder.saved", { path: target }));
    setLastRecallQuery(target);
    await completeIngestion("folder", result.data);
    triggerBrainRecall();
  }, [beginIngestion, completeIngestion, failIngestion, language, onBrainChange, setLastRecallQuery, setMemoryFeedback]);

  // The folder button used to call the desktop-only picker, so in a browser it
  // resolved to null and the button did nothing. Route through the shared
  // picker, which falls back to reading the directory in the browser.
  const pickFolder = React.useCallback(async () => {
    const selection = await pickFolderSelection();
    if (selection.kind === "cancelled") return;
    if (selection.kind === "unavailable") {
      setMemoryFeedback(t(language, "brain.ingest.folder.unavailable"));
      return;
    }
    if (selection.kind === "path") {
      await connectFolder(selection.path);
      return;
    }

    const folderName = selection.name || browserFolderNameFromFiles(selection.files);
    if (!selection.files.length) {
      setMemoryFeedback(t(language, "brain.ingest.folder.empty"));
      failIngestion("folder", "empty");
      return;
    }

    // No server-side path to watch here — the browser hands us the files, so
    // each one goes in as a document and the folder tile reports the batch.
    setMemoryFeedback(t(language, "brain.ingest.folder.reading"));
    onBrainChange("recalling", 0.84);
    beginIngestion("folder", folderName);
    let added = 0;
    let lastResult: Awaited<ReturnType<typeof latticeApi.uploadDocument>> | null = null;
    for (const file of selection.files) {
      const result = await latticeApi.uploadDocument(file);
      if (result.ok) {
        added += 1;
        lastResult = result;
      }
    }
    if (!added) {
      setMemoryFeedback(t(language, "brain.ingest.folder.empty"));
      failIngestion("folder", "empty");
      return;
    }
    setMemoryFeedback(t(language, "brain.ingest.folder.browserSaved", { name: folderName, count: added }));
    setLastRecallQuery(folderName);
    await completeIngestion("folder", lastResult?.data);
    triggerBrainRecall();
  }, [
    beginIngestion,
    completeIngestion,
    connectFolder,
    failIngestion,
    language,
    onBrainChange,
    setLastRecallQuery,
    setMemoryFeedback,
  ]);

  const ingestNote = React.useCallback(async (note: string) => {
    const content = note.trim();
    if (!content) return;
    setMemoryFeedback(t(language, "brain.ingest.note.pending"));
    onBrainChange("recalling", 0.84);
    beginIngestion("note", content.slice(0, 80));
    const result = await latticeApi.ingestNote(content, content.slice(0, 80));
    if (!result.ok) {
      const reason = result.error || "unavailable";
      setMemoryFeedback(t(language, "brain.ingest.note.failed", { reason }));
      failIngestion("note", reason);
      return;
    }
    setMemoryFeedback(t(language, "brain.ingest.note.saved"));
    setLastRecallQuery(content.slice(0, 120));
    await completeIngestion("note", result.data);
    triggerBrainRecall();
  }, [beginIngestion, completeIngestion, failIngestion, language, onBrainChange, setLastRecallQuery, setMemoryFeedback]);

  const ingestWeb = React.useCallback(async (url: string) => {
    const target = url.trim();
    if (!target) return;
    setMemoryFeedback(t(language, "brain.ingest.web.pending", { url: target }));
    onBrainChange("recalling", 0.84);
    beginIngestion("web", target);
    const result = await latticeApi.browserReadUrl(target);
    const payload = result.data && typeof result.data === "object" ? result.data as Record<string, unknown> : {};
    const ingestionStatus = typeof payload.status === "string" ? payload.status : "";
    if (!result.ok || ingestionStatus !== "ok") {
      const reason = result.error
        || (typeof payload.detail === "string" ? payload.detail : ingestionStatus)
        || "unavailable";
      setMemoryFeedback(t(language, "brain.ingest.web.failed", { reason }));
      failIngestion("web", reason);
      return;
    }
    setMemoryFeedback(t(language, "brain.ingest.web.saved", { url: target }));
    setLastRecallQuery(target);
    await completeIngestion("web", result.data);
    triggerBrainRecall();
  }, [beginIngestion, completeIngestion, failIngestion, language, onBrainChange, setLastRecallQuery, setMemoryFeedback]);

  return {
    ingestionStates,
    emergenceEvents,
    synthesisActive,
    uploadingDocument,
    beginIngestion,
    completeIngestion,
    failIngestion,
    uploadDocument,
    pickFolder,
    connectFolder,
    ingestNote,
    ingestWeb,
    resetChatIngestion: () => setIngestionStates((states) => ({ ...states, chat: null })),
  };
}
