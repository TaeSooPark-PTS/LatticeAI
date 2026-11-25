import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { latticeApi } from "@/api/client";
import { t, type Language } from "@/i18n";
import {
  type BrowserDirectoryPickerWindow,
  type UploadQueueItem,
  browserFolderNameFromFiles,
  displayFileName,
  filesFromBrowserDirectory,
  hasDesktopFolderPicker,
  isAbortError,
  normalizeWebUrl,
  readJourney,
} from "../captureDomain";

async function uploadFiles(files: File[], setQueue: React.Dispatch<React.SetStateAction<UploadQueueItem[]>>) {
  const rows = files.map((file) => ({
    id: `${file.name}-${file.size}-${file.lastModified}-${Date.now()}`,
    file,
    name: displayFileName(file),
    size: file.size,
    status: "queued" as const,
  }));
  setQueue((items) => [...rows, ...items].slice(0, 12));
  const results = [];
  for (const row of rows) {
    setQueue((items) => items.map((item) => item.id === row.id ? { ...item, status: "uploading" } : item));
    const result = await latticeApi.uploadDocument(row.file);
    results.push(result);
    setQueue((items) => items.map((item) => item.id === row.id ? { ...item, status: result.ok ? "done" : "failed", result } : item));
  }
  return results;
}

export function useFileIntake() {
  const qc = useQueryClient();
  const [queue, setQueue] = React.useState<UploadQueueItem[]>([]);
  const upload = useMutation({
    mutationFn: (files: File[]) => uploadFiles(files, setQueue),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["documents"] });
      void qc.invalidateQueries({ queryKey: ["graphStats"] });
      void qc.invalidateQueries({ queryKey: ["memoryManager"] });
    },
  });
  const beginUpload = React.useCallback((files: FileList | File[]) => {
    const nextFiles = Array.from(files);
    if (!nextFiles.length) return;
    upload.mutate(nextFiles);
  }, [upload]);

  return { queue, beginUpload };
}

export function useFolderIntake(language: Language) {
  const qc = useQueryClient();
  const folderInputRef = React.useRef<HTMLInputElement>(null);
  const [path, setPath] = React.useState("");
  const [folderPickError, setFolderPickError] = React.useState<string | null>(null);
  const [browserFolderName, setBrowserFolderName] = React.useState("");
  const [choosingFolder, setChoosingFolder] = React.useState(false);
  const [folderQueue, setFolderQueue] = React.useState<UploadQueueItem[]>([]);

  const connect = useMutation({
    mutationFn: (targetPath: string) => latticeApi.connectFolder(targetPath.trim()),
    onSuccess: () => {
      setFolderPickError(null);
      void qc.invalidateQueries({ queryKey: ["localSources"] });
      void qc.invalidateQueries({ queryKey: ["graphStats"] });
      void qc.invalidateQueries({ queryKey: ["memoryManager"] });
    },
  });
  const browserFolderUpload = useMutation({
    mutationFn: (files: File[]) => uploadFiles(files, setFolderQueue),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["documents"] });
      void qc.invalidateQueries({ queryKey: ["graphStats"] });
      void qc.invalidateQueries({ queryKey: ["memoryManager"] });
    },
  });
  const beginBrowserFolderUpload = React.useCallback((files: File[], folderName?: string) => {
    if (!files.length) {
      setFolderPickError(t(language, "capture.local.emptyFolder"));
      return;
    }
    setFolderPickError(null);
    setBrowserFolderName(folderName || browserFolderNameFromFiles(files) || t(language, "capture.local.browserFolder"));
    browserFolderUpload.mutate(files);
  }, [browserFolderUpload, language]);
  const chooseFolder = React.useCallback(async () => {
    setChoosingFolder(true);
    setFolderPickError(null);
    try {
      const browserPicker = (window as BrowserDirectoryPickerWindow).showDirectoryPicker;
      if (!hasDesktopFolderPicker() && typeof browserPicker === "function") {
        const handle = await browserPicker.call(window);
        beginBrowserFolderUpload(await filesFromBrowserDirectory(handle), handle.name);
        return;
      }
      if (hasDesktopFolderPicker()) {
        const selectedPath = await latticeApi.selectFolder();
        if (selectedPath) {
          setBrowserFolderName("");
          setPath(selectedPath);
          connect.mutate(selectedPath);
          return;
        }
      }
      if (typeof browserPicker === "function") {
        const handle = await browserPicker.call(window);
        beginBrowserFolderUpload(await filesFromBrowserDirectory(handle), handle.name);
        return;
      }
      if (folderInputRef.current) {
        folderInputRef.current.click();
        return;
      }
      setFolderPickError(t(language, "capture.local.pickUnavailable"));
    } catch (error) {
      if (!isAbortError(error)) setFolderPickError(t(language, "capture.local.pickUnavailable"));
    } finally {
      setChoosingFolder(false);
    }
  }, [beginBrowserFolderUpload, choosingFolder, connect, language]);
  const connectCurrent = React.useCallback(() => {
    const target = path.trim();
    if (!target) return;
    setFolderPickError(null);
    setBrowserFolderName("");
    connect.mutate(target);
  }, [connect, path]);

  const browserFolderResults = browserFolderUpload.data || [];
  const browserFolderResultError = browserFolderResults.find((result) => !result.ok)?.error;
  const browserFolderFailed = Boolean(browserFolderUpload.error || browserFolderResultError);

  return {
    folderInputRef,
    path,
    setPath,
    folderPickError,
    browserFolderName,
    choosingFolder,
    folderQueue,
    connect,
    browserFolderUpload,
    beginBrowserFolderUpload,
    chooseFolder,
    connectCurrent,
    browserFolderResultError,
    browserFolderFailed,
  };
}

export function useWebIntake() {
  const qc = useQueryClient();
  const [url, setUrl] = React.useState("");
  const read = useMutation({
    mutationFn: (targetUrl: string) => latticeApi.browserReadUrl(targetUrl),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["graphStats"] });
      void qc.invalidateQueries({ queryKey: ["memoryManager"] });
    },
  });
  const captureUrl = React.useCallback(() => {
    const target = normalizeWebUrl(url);
    if (!target) return;
    setUrl(target);
    read.mutate(target);
  }, [read, url]);
  const pasteUrl = React.useCallback(async () => {
    const text = await navigator.clipboard?.readText().catch(() => "");
    const target = normalizeWebUrl(text || "");
    if (!target) return;
    setUrl(target);
    read.mutate(target);
  }, [read]);

  return { url, setUrl, read, captureUrl, pasteUrl };
}

export function useCapturePipeline() {
  const index = useQuery({ queryKey: ["index"], queryFn: latticeApi.indexStatus });
  const stats = useQuery({ queryKey: ["graphStats"], queryFn: latticeApi.graphStats });
  const pipelineStatus = useQuery({ queryKey: ["pipelineStatus"], queryFn: latticeApi.pipelineStatus });
  const statusData = (pipelineStatus.data?.data || pipelineStatus.data);
  const journey = readJourney(statusData, index.data?.data, stats.data?.data);
  const hasPipelineData = Boolean(
    journey.remembered
    || journey.connections
    || (journey.received && journey.received > 0),
  );

  return { index, stats, journey, hasPipelineData };
}

export function useRecentCaptures() {
  const docs = useQuery({ queryKey: ["documents"], queryFn: () => latticeApi.documents(200) });
  const local = useQuery({ queryKey: ["localSources"], queryFn: latticeApi.localSources });
  const agent = useQuery({ queryKey: ["localAgent"], queryFn: latticeApi.localAgent });
  return { docs, local, agent };
}
