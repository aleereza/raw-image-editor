// Types and calls for the `rie serve` API. Everything is JSON except the images.

export type Pile = "your_call" | "auto_reject" | "auto_keep";
export type Decision = "keep" | "reject" | null;

export interface Photo {
  id: number;
  name: string;
  jpeg: string | null;
  time: string | null;
  state: "present" | "trashed" | "missing";
  w: number | null;
  h: number | null;
  v: string;
  pile: Pile | null;
  suggestion: "keep" | "reject" | null;
  kind: string | null;
  reason: string | null;
  p: number | null;
  decision: Decision;
  rating: number | null;
  owner_reason: string | null;
  scene: number | null;
  burst: { id: number | null; size: number; rank: number; position: number; best: number | null; issues: string[] };
  focus: { verdict: string | null; blur: number | null; subject: string | null; reason: string | null };
  faces: number;
  closed: number;
  away: number;
  smile: number | null;
  clip: number | null;
  lens: string | null;
  settings: string;
  unmodeled: number;
  edits: boolean;
}

export interface Folder {
  id: number;
  name: string;
  path: string;
  on_drive: boolean;
  available: boolean;
  photos: number;
  trashed: number;
  in_trash: number;
  opened_at: string;
  analyzed_at: string | null;
  culled_at: string | null;
}

export interface FolderDetail {
  id: number;
  name: string;
  path: string;
  available: boolean;
  culled_at: string | null;
  piles: Record<Pile, number>;
  decided: number;
  metrics: {
    photos: number;
    automatic: number;
    changed: number;
    share_automatic: number;
    change_rate: number;
    minutes: number;
    agreement: Record<string, { agreed: number; total: number }>;
  } | null;
  trust: Record<string, "suggest" | "automatic">;
  proposals: { kind: string; agreement: number; photos: number; folders: number }[];
  model: { id: number; agreement: number; baseline: number; n_train: number } | null;
}

export interface Status {
  drive: { state: string; detail: string; configured: boolean };
  space: { trash_files: number; trash_bytes: number; free_bytes: number; total_bytes: number; low: boolean; text: string } | null;
  trash_autoempty: string[];
  face_models: boolean;
  photos_root: string | null;
}

export interface Face {
  idx: number;
  box: [number, number, number, number];
  eyes: [number, number][];
  blink: number | null;
  smile: number | null;
  yaw: number | null;
  sharpness: number | null;
  cut_off: boolean;
  track: number | null;
}

export interface Analysis {
  heat: { rows: number; cols: number; blur: (number | null)[] } | null;
  faces: Face[];
  focus: {
    verdict?: string;
    subject?: string;
    subject_blur?: number | null;
    background_blur?: number | null;
    subject_boxes?: [number, number, number, number][];
    af_box?: [number, number, number, number] | null;
    reason?: string;
  };
  burst: Record<string, unknown>;
  look: Record<string, unknown>;
  recipe: { params: Record<string, Record<string, unknown>> } | null;
  unmodeled: string[];
}

export interface Job {
  id: number;
  kind: string;
  path: string;
  state: "queued" | "running" | "done" | "failed";
  step: string;
  done: number;
  total: number;
  message: string;
  folder_id: number | null;
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export const api = {
  status: () => request<Status>("GET", "/api/status"),
  folders: () => request<Folder[]>("GET", "/api/folders"),
  folder: (id: number) => request<FolderDetail>("GET", `/api/folders/${id}`),
  photos: (id: number) => request<Photo[]>("GET", `/api/folders/${id}/photos`),
  open: (path: string) => request<Job>("POST", "/api/folders/open", { path }),
  analysis: (id: number) => request<Analysis>("GET", `/api/photos/${id}/analysis`),
  decide: (id: number, decision: Decision, reason?: string) =>
    request<{ ok: boolean }>("POST", `/api/photos/${id}/decision`, { decision, reason: reason ?? null }),
  reason: (id: number, reason: string) => request<{ ok: boolean }>("POST", `/api/photos/${id}/reason`, { reason }),
  rate: (id: number, rating: number) =>
    request<{ ok: boolean; sidecar: string }>("POST", `/api/photos/${id}/rating`, { rating }),
  pick: (id: number, keepOthers: boolean) =>
    request<{ ok: boolean; changed: number[] }>("POST", `/api/photos/${id}/pick`, { keep_others: keepOthers }),
  donePreview: (id: number) =>
    request<{ rejects: number; bytes: number; size: string; keeps: number; undecided: number; auto: number; files: number }>(
      "GET",
      `/api/folders/${id}/done`,
    ),
  done: (id: number) =>
    request<{ trashed: number; files: number; size: string; kept: number; errors: string[]; model: { promoted: boolean; note: string } | null }>(
      "POST",
      `/api/folders/${id}/done`,
    ),
  restoreFolder: (id: number) => request<{ restored: number }>("POST", `/api/folders/${id}/restore`),
  setTrust: (kind: string, mode: string) => request<{ modes: Record<string, string> }>("POST", "/api/trust", { kind, mode }),
};

export const urls = {
  thumb: (p: Photo) => `/api/photos/${p.id}/thumb?v=${p.v}`,
  screen: (p: Photo) => `/api/photos/${p.id}/screen?v=${p.v}`,
  full: (p: Photo) => `/api/photos/${p.id}/full?v=${p.v}`,
  face: (id: number, idx: number, v: string) => `/api/photos/${id}/face/${idx}?v=${v}`,
};
