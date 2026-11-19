// Shared UI state (Svelte 5 runes).
import type { Analysis, FolderDetail, Job, Photo, Pile, Status } from "./api";
import { api } from "./api";

export const PILES: { id: Pile; label: string }[] = [
  { id: "your_call", label: "Your call" },
  { id: "auto_reject", label: "Auto-reject" },
  { id: "auto_keep", label: "Auto-keep" },
];

export const REASONS = ["blur", "eyes", "expression", "duplicate", "composition"] as const;

export const ui = $state({
  view: "folders" as "folders" | "cull",
  mode: "grid" as "grid" | "loupe" | "compare",
  folderId: null as number | null,
  folder: null as FolderDetail | null,
  photos: [] as Photo[],
  pile: "your_call" as Pile,
  currentId: null as number | null,
  heat: false,
  zoom: false,
  help: false,
  debug: false,
  done: false,
  status: null as Status | null,
  jobs: [] as Job[],
  message: "",
  reasonFor: null as number | null,
  offline: false, // `rie serve` cannot be reached
});

const analyses = new Map<number, Promise<Analysis>>();

export function analysisFor(id: number): Promise<Analysis> {
  let hit = analyses.get(id);
  if (!hit) {
    hit = api.analysis(id);
    analyses.set(id, hit);
    hit.catch(() => analyses.delete(id));
  }
  return hit;
}

let messageTimer: ReturnType<typeof setTimeout> | undefined;
export function say(text: string, ms = 4000): void {
  ui.message = text;
  clearTimeout(messageTimer);
  messageTimer = setTimeout(() => (ui.message = ""), ms);
}

export async function loadFolder(id: number): Promise<void> {
  const [folder, photos] = await Promise.all([api.folder(id), api.photos(id)]);
  ui.folderId = id;
  ui.folder = folder;
  ui.photos = photos;
  ui.view = "cull";
  if (!photos.some((p) => p.id === ui.currentId)) {
    const first = PILES.map((pile) => photos.find((p) => p.state === "present" && p.pile === pile.id)).find(Boolean);
    ui.pile = (first?.pile as Pile) ?? "your_call";
    ui.currentId = photos.find((p) => p.state === "present" && p.pile === ui.pile)?.id ?? null;
  }
}

export async function refreshFolder(): Promise<void> {
  if (ui.folderId !== null) await loadFolder(ui.folderId);
}

// Bursts in the current pile: one tile per burst (its best-ranked frame in this pile) with a count.
export interface Tile {
  photo: Photo;
  frames: Photo[];
}

export function tilesOf(items: Photo[]): Tile[] {
  const byKey = new Map<string, Photo[]>();
  for (const p of items) {
    const key = p.burst.id !== null && p.burst.size > 1 ? `b${p.burst.id}` : `p${p.id}`;
    const list = byKey.get(key);
    if (list) list.push(p);
    else byKey.set(key, [p]);
  }
  return [...byKey.values()].map((frames) => ({
    photo: frames.reduce((best, p) => (p.burst.rank < best.burst.rank ? p : best), frames[0]),
    frames,
  }));
}

export function burstOf(photo: Photo, photos: Photo[]): Photo[] {
  if (photo.burst.id === null || photo.burst.size <= 1) return [photo];
  return photos.filter((p) => p.burst.id === photo.burst.id && p.state === "present").sort((a, b) => a.burst.position - b.burst.position);
}

export function fileSize(bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = bytes;
  let i = 0;
  while (n >= 1000 && i < units.length - 1) {
    n /= 1000;
    i += 1;
  }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}
