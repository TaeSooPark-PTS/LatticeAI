import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Network, RefreshCw, Send, Share2, Trash2, Upload } from "lucide-react";
import { brainNetworkApi } from "@/api/brainNetwork";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { t, type Language } from "@/i18n";

export function BrainNetworkPanel({ language }: { language: Language }) {
  const queryClient = useQueryClient();
  const [name, setName] = React.useState("");
  const [baseUrl, setBaseUrl] = React.useState("");
  const [publicKey, setPublicKey] = React.useState("");
  const [queryFilter, setQueryFilter] = React.useState("");
  const [importJson, setImportJson] = React.useState("");
  const [feedback, setFeedback] = React.useState<string | null>(null);
  const [copied, setCopied] = React.useState(false);

  const shareQuery = useQuery({
    queryKey: ["brain-network-share-status"],
    queryFn: () => brainNetworkApi.shareStatus(),
  });
  const identityQuery = useQuery({
    queryKey: ["brain-network-identity"],
    queryFn: () => brainNetworkApi.identity(),
  });
  const peersQuery = useQuery({
    queryKey: ["brain-network-peers"],
    queryFn: () => brainNetworkApi.peers(),
  });

  const enabled = Boolean(shareQuery.data?.data?.enabled);
  const device = identityQuery.data?.data?.fingerprint
    ? identityQuery.data.data
    : shareQuery.data?.data?.device;
  const peers = peersQuery.data?.data?.peers ?? [];

  const refreshAll = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["brain-network-share-status"] }),
      queryClient.invalidateQueries({ queryKey: ["brain-network-identity"] }),
      queryClient.invalidateQueries({ queryKey: ["brain-network-peers"] }),
    ]);

  function actionError(error: string | undefined, fallback: string) {
    setFeedback(error || fallback);
  }

  const pairMutation = useMutation({
    mutationFn: () =>
      brainNetworkApi.pairPeer({
        name: name.trim(),
        base_url: baseUrl.trim(),
        public_key: publicKey.trim(),
      }),
    onSuccess: async (res) => {
      if (!res.ok) {
        actionError(res.error, "Pair failed");
        return;
      }
      setName("");
      setBaseUrl("");
      setPublicKey("");
      setFeedback(res.data.peer?.name || res.data.status);
      await refreshAll();
    },
  });

  const unpairMutation = useMutation({
    mutationFn: (peerId: string) => brainNetworkApi.unpairPeer(peerId),
    onSuccess: async (res) => {
      if (!res.ok) {
        actionError(res.error, "Unpair failed");
        return;
      }
      await refreshAll();
    },
  });

  const pushMutation = useMutation({
    mutationFn: (peerId: string) => brainNetworkApi.pushPeer(peerId),
    onSuccess: (res) => {
      if (!res.ok) {
        actionError(res.error, "Push failed");
        return;
      }
      setFeedback(String(res.data.status || "pushed"));
    },
  });

  const exportMutation = useMutation({
    mutationFn: () =>
      brainNetworkApi.shareExport(
        queryFilter.trim() ? { query: queryFilter.trim(), limit: 40 } : { limit: 40 },
      ),
    onSuccess: (res) => {
      if (!res.ok) {
        actionError(res.error, "Export failed");
        return;
      }
      const counts = res.data.counts ?? {};
      const nodes = Number(counts.nodes ?? 0);
      const edges = Number(counts.edges ?? 0);
      setFeedback(t(language, "brain.network.exportedSummary", { nodes, edges }));
    },
  });

  const importMutation = useMutation({
    mutationFn: (artifact: Record<string, unknown>) =>
      brainNetworkApi.shareImport({ artifact, dry_run: false }),
    onSuccess: (res) => {
      if (!res.ok) {
        actionError(res.error, "Import failed");
        return;
      }
      setImportJson("");
      setFeedback(
        t(language, "brain.network.importStaged", {
          proposalId: res.data.proposal_id || "pending",
        }),
      );
    },
  });

  async function copyPublicKey(key: string) {
    await navigator.clipboard?.writeText(key);
    setCopied(true);
  }

  function handleImport() {
    try {
      const parsed = JSON.parse(importJson) as unknown;
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        importMutation.mutate(parsed as Record<string, unknown>);
      } else {
        setFeedback("JSON must be a subgraph bundle object");
      }
    } catch {
      setFeedback("Invalid JSON bundle");
    }
  }

  return (
    <Card data-testid="brain-network-panel">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <Network className="h-4 w-4" aria-hidden="true" />
            {t(language, "brain.network.title")}
          </CardTitle>
          <CardDescription>{t(language, "brain.network.subtitle")}</CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={enabled ? "default" : "muted"}>
            {t(language, enabled ? "brain.network.enabledBadge" : "brain.network.disabledBadge")}
          </Badge>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void refreshAll()}
          >
            <RefreshCw className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
            {t(language, "brain.gov.refresh")}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {!enabled ? (
          <p
            className="rounded-md border border-amber-300/60 bg-amber-50/40 p-2.5 text-xs text-muted-foreground"
            data-testid="brain-network-disabled-banner"
          >
            {t(language, "brain.network.disabledBanner")}
          </p>
        ) : null}

        {feedback ? (
          <p className="rounded-md border bg-muted/40 p-2 text-xs" role="status" data-testid="brain-network-feedback">
            {feedback}
          </p>
        ) : null}

        <section className="rounded-md border p-3 space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />
              {t(language, "brain.network.identityTitle")}
            </span>
            {device?.public_key ? (
              <Button type="button" size="sm" variant="outline" onClick={() => void copyPublicKey(device.public_key)}>
                <Copy className="mr-1 h-3 w-3" aria-hidden="true" />
                {t(language, copied ? "brain.network.copied" : "brain.network.copyKey")}
              </Button>
            ) : null}
          </div>
          <p className="text-xs">
            <strong>{t(language, "brain.network.fingerprint")}:</strong>{" "}
            <code data-testid="device-fingerprint">{device?.fingerprint || "-"}</code>
          </p>
          <p className="truncate text-xs text-muted-foreground">
            <strong>{t(language, "brain.network.publicKey")}:</strong>{" "}
            <code data-testid="device-public-key">{device?.public_key || "-"}</code>
          </p>
        </section>

        <section className="space-y-2">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t(language, "brain.network.peersTitle")} ({peers.length})
          </h4>
          {peers.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t(language, "brain.network.noPeers")}</p>
          ) : (
            <ul className="space-y-2">
              {peers.map((peer) => (
                <li
                  key={peer.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-2.5 text-sm"
                >
                  <div>
                    <div className="font-medium">{peer.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {peer.base_url} · {peer.fingerprint}
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={pushMutation.isPending}
                      onClick={() => pushMutation.mutate(peer.id)}
                    >
                      <Send className="mr-1 h-3 w-3" aria-hidden="true" />
                      {t(language, "brain.network.push")}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={unpairMutation.isPending}
                      onClick={() => unpairMutation.mutate(peer.id)}
                    >
                      <Trash2 className="mr-1 h-3 w-3" aria-hidden="true" />
                      {t(language, "brain.network.unpair")}
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}

          <form
            className="grid gap-2 md:grid-cols-3"
            onSubmit={(event) => {
              event.preventDefault();
              pairMutation.mutate();
            }}
          >
            <Input
              aria-label={t(language, "brain.network.peerName")}
              placeholder={t(language, "brain.network.peerName")}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            <Input
              aria-label={t(language, "brain.network.peerUrl")}
              placeholder={t(language, "brain.network.peerUrl")}
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
            />
            <Input
              aria-label={t(language, "brain.network.peerKey")}
              placeholder={t(language, "brain.network.peerKey")}
              value={publicKey}
              onChange={(event) => setPublicKey(event.target.value)}
            />
            <div className="md:col-span-3">
              <Button
                type="submit"
                size="sm"
                disabled={!name.trim() || !baseUrl.trim() || !publicKey.trim() || pairMutation.isPending}
              >
                {t(language, "brain.network.pairPeer")}
              </Button>
            </div>
          </form>
        </section>

        <section className="space-y-2 rounded-md border p-3">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t(language, "brain.network.exportTitle")}
          </h4>
          <div className="flex flex-wrap gap-2">
            <Input
              aria-label={t(language, "brain.network.queryPlaceholder")}
              placeholder={t(language, "brain.network.queryPlaceholder")}
              value={queryFilter}
              onChange={(event) => setQueryFilter(event.target.value)}
            />
            <Button
              type="button"
              size="sm"
              disabled={!enabled || exportMutation.isPending}
              onClick={() => exportMutation.mutate()}
            >
              <Share2 className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
              {t(language, "brain.network.exportBtn")}
            </Button>
          </div>
          <Textarea
            aria-label={t(language, "brain.network.importPlaceholder")}
            placeholder={t(language, "brain.network.importPlaceholder")}
            value={importJson}
            onChange={(event) => setImportJson(event.target.value)}
          />
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs text-muted-foreground">{t(language, "brain.network.reviewNote")}</span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={!enabled || !importJson.trim() || importMutation.isPending}
              onClick={handleImport}
            >
              <Upload className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
              {t(language, "brain.network.importBtn")}
            </Button>
          </div>
        </section>
      </CardContent>
    </Card>
  );
}
