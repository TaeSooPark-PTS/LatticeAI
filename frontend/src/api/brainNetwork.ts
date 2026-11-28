import { del, get, post, type ApiResult } from "./base";

export type DeviceIdentity = {
  fingerprint: string;
  public_key: string;
  algorithm: string;
  storage?: string;
};

export type PeerRecord = {
  id: string;
  name: string;
  base_url: string;
  public_key: string;
  fingerprint: string;
  added_at?: string;
};

export type PeersResponse = {
  peers: PeerRecord[];
};

export type PairPeerRequest = {
  name: string;
  base_url: string;
  public_key: string;
};

export type PairPeerResponse = {
  status: string;
  peer: PeerRecord;
};

export type ShareStatusResponse = {
  enabled: boolean;
  flag?: string;
  format?: string;
  format_version?: number;
  graph_available?: boolean;
  signing?: boolean;
  device?: DeviceIdentity;
  sealed_box_algorithm?: string;
  detail?: string | null;
};

export type ShareExportRequest = {
  workspace_id?: string;
  query?: string;
  limit?: number;
};

export type ShareExportResponse = {
  format?: string;
  format_version?: number;
  counts?: Record<string, number>;
  excluded_local_only?: number;
  signed?: boolean;
  signature_verified?: boolean;
  bundle?: Record<string, unknown>;
  artifact?: Record<string, unknown>;
};

export type ShareImportRequest = {
  artifact?: Record<string, unknown>;
  path?: string;
  workspace_id?: string;
  passphrase?: string;
  dry_run?: boolean;
};

export type ShareImportResponse = {
  staged: boolean;
  status: string;
  dry_run?: boolean;
  signature_verified?: boolean;
  sender_trusted?: boolean;
  proposal_id?: string;
  counts?: Record<string, number>;
};

const EMPTY_IDENTITY: DeviceIdentity = {
  fingerprint: "",
  public_key: "",
  algorithm: "ed25519",
};

const EMPTY_SHARE_STATUS: ShareStatusResponse = {
  enabled: false,
  flag: "LATTICEAI_BRAIN_NETWORK",
  graph_available: true,
  signing: true,
};

const EMPTY_PEERS: PeersResponse = {
  peers: [],
};

export const brainNetworkApi = {
  shareStatus: (): Promise<ApiResult<ShareStatusResponse>> =>
    get<ShareStatusResponse>("/api/knowledge-graph/share", EMPTY_SHARE_STATUS),
  identity: (): Promise<ApiResult<DeviceIdentity>> =>
    get<DeviceIdentity>("/network/identity", EMPTY_IDENTITY),
  peers: (): Promise<ApiResult<PeersResponse>> =>
    get<PeersResponse>("/network/peers", EMPTY_PEERS),
  pairPeer: (body: PairPeerRequest): Promise<ApiResult<PairPeerResponse>> =>
    post<PairPeerResponse>("/network/peers", body, {} as PairPeerResponse),
  unpairPeer: (peerId: string): Promise<ApiResult<Record<string, unknown>>> =>
    del<Record<string, unknown>>(`/network/peers/${encodeURIComponent(peerId)}`, {}),
  pushPeer: (
    peerId: string,
    workspaceId?: string | null,
  ): Promise<ApiResult<Record<string, unknown>>> =>
    post<Record<string, unknown>>(
      `/network/push/${encodeURIComponent(peerId)}`,
      workspaceId ? { workspace_id: workspaceId } : {},
      {},
    ),
  shareExport: (body: ShareExportRequest): Promise<ApiResult<ShareExportResponse>> =>
    post<ShareExportResponse>(
      "/api/knowledge-graph/share/export",
      body,
      {} as ShareExportResponse,
    ),
  shareImport: (body: ShareImportRequest): Promise<ApiResult<ShareImportResponse>> =>
    post<ShareImportResponse>(
      "/api/knowledge-graph/share/import",
      body,
      {} as ShareImportResponse,
    ),
};
