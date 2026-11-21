// Types and calls for Phase 2's review page: edited photos, the owner's review actions, export and the look lab.

export type Params = Record<string, Record<string, unknown>>;

export interface Proposal {
  agent: string;
  params: Params;
  applied: Params;
  confidence: number;
  band: "apply" | "conservative" | "as_shot" | "abstained" | "locked";
  why: string;
  flags: string[];
}

export interface Check {
  name: string;
  ok: boolean;
  message: string;
}

export interface Edit {
  id: number;
  name: string;
  time: string | null;
  scene: number | null;
  look: string;
  review: boolean;
  reviewed: boolean;
  reasons: string[];
  qa: Check[];
  has_before: boolean;
  has_after: boolean;
  version: number;
  params: Params;
  provenance: Record<string, { source?: string; locked?: boolean; why?: string; confidence?: number; band?: string }>;
  proposals: Proposal[];
  v: string;
}

export interface LookDef {
  id: string;
  name: string;
  version: number;
  curve: [number, number][];
  curve_mode: string;
  contrast: number;
  saturation: number;
  vibrance: number;
  warmth: number;
  description: string;
}

export interface FolderEdits {
  folder: { id: number; name: string; available: boolean };
  edits: Edit[];
  looks: LookDef[];
  exports: { count: number; last: string | null };
  no_override_share: number | null;
}

export interface Variation {
  label: string;
  look: LookDef;
  key: string;
}

export interface Job {
  id: number;
  kind: string;
  state: string;
  step: string;
  done: number;
  total: number;
  message: string;
  folder_id: number | null;
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error("cannot reach the Raw Image Editor server: is `uv run rie serve` still running?");
  }
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

export const reviewApi = {
  edits: (folder: number) => request<FolderEdits>("GET", `/api/folders/${folder}/edits`),
  edit: (folder: number, all: boolean) => request<Job>("POST", `/api/folders/${folder}/edit`, { all }),
  exportFolder: (folder: number) => request<Job>("POST", `/api/folders/${folder}/export`),
  nudge: (id: number, kind: string, steps: number) => request<Edit>("POST", `/api/photos/${id}/nudge`, { kind, steps }),
  reset: (id: number, group: string) => request<Edit>("POST", `/api/photos/${id}/reset`, { group }),
  lock: (id: number, key: string, locked: boolean) => request<Edit>("POST", `/api/photos/${id}/lock`, { key, locked }),
  reviewed: (id: number, reviewed: boolean) => request<Edit>("POST", `/api/photos/${id}/reviewed`, { reviewed }),
  scene: (id: number, groups: string[]) =>
    request<{ changed: number[]; photo: Edit }>("POST", `/api/photos/${id}/scene`, { groups }),
  lookLab: (id: number, look: LookDef | null, round: number) =>
    request<{ round: number; variations: Variation[] }>("POST", `/api/photos/${id}/looklab`, { look, round }),
  saveLook: (name: string, look: LookDef, description: string) =>
    request<LookDef>("POST", "/api/looks", { name, look, description }),
  useLook: (id: number, look: string, scene: boolean) =>
    request<{ changed: number[]; photo: Edit }>("POST", `/api/photos/${id}/look`, { look, scene }),
  rawtherapee: (id: number) => request<{ opened: string }>("POST", `/api/photos/${id}/rawtherapee`),
};

export const reviewUrls = {
  preview: (e: Edit, kind: "before" | "after") => `/api/photos/${e.id}/preview/${kind}?v=${encodeURIComponent(e.v)}`,
  thumb: (e: Edit) => `/api/photos/${e.id}/thumb`,
  variation: (v: Variation) => `/api/looklab/${v.key}`,
};
