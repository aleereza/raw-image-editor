// Shared UI state (Svelte 5 runes).
import type { Analysis, FolderDetail, Job, Photo, Status } from "./api";
import { api } from "./api";

// The culling page shows every photo; a filter narrows it to the open photos or to one of the app's automatic
// piles (ADR 0011). A burst shows when any of its frames matches.
export type Filter = "open" | "app_reject" | "app_keep";
export const FILTERS: { id: Filter; label: string; title: string }[] = [
  { id: "open", label: "To decide", title: "Photos and bursts nobody has decided yet" },
  { id: "app_reject", label: "Rejected by the app", title: "What the app rejects on its own (its Auto-reject pile)" },
  { id: "app_keep", label: "Kept by the app", title: "What the app keeps on its own (its Auto-keep pile)" },
];

export const REASONS = ["blur", "eyes", "expression", "duplicate", "composition"] as const;

// Everything the culling page can do; the keys and the action panel both call these.
export interface CullActions {
  keep: () => void; // a single photo: keep it; a frame of a burst (or a burst tile's frame shown): pick it
  reject: () => void; // a burst tile in the grid: reject every frame
  clear: () => void; // a burst tile in the grid: clear every frame
  previous: () => void;
  next: () => void;
  up: () => void;
  down: () => void;
  nextOpen: () => void;
  previousOpen: () => void;
  nextScene: () => void;
  previousScene: () => void;
  nextFilter: () => void;
  previousFilter: () => void;
  clearFilter: () => void;
  open: () => void; // Enter: from the grid, a single photo's loupe or a burst's compare; elsewhere, back to the grid
  zoom: () => void;
  focusMap: () => void;
  compare: () => void;
  acceptPick: () => void;
  back: () => void;
  help: () => void;
  timings: () => void;
}

// What Done culling will do with a photo, and who decided: the owner's decision, else the app's automatic pile,
// else open (nobody decided; it is kept).
export type Verdict = "keep" | "reject" | "open";
export interface Mark {
  verdict: Verdict;
  by: "you" | "app" | null;
}

export function markOf(photo: Photo): Mark {
  if (photo.decision) return { verdict: photo.decision, by: "you" };
  if (photo.pile === "auto_keep") return { verdict: "keep", by: "app" };
  if (photo.pile === "auto_reject") return { verdict: "reject", by: "app" };
  return { verdict: "open", by: null };
}

export function markText(mark: Mark): string {
  if (mark.verdict === "open") return "to decide";
  return `${mark.verdict === "keep" ? "kept" : "rejected"} by ${mark.by === "you" ? "you" : "the app"}`;
}

// A burst's own mark: kept once a frame is kept and none is open, rejected when every frame is, else open.
export function burstMark(frames: Photo[]): Mark {
  const marks = frames.map(markOf);
  const kept = marks.filter((m) => m.verdict === "keep");
  if (kept.length && !marks.some((m) => m.verdict === "open")) {
    return { verdict: "keep", by: kept.some((m) => m.by === "you") ? "you" : "app" };
  }
  if (marks.every((m) => m.verdict === "reject")) {
    return { verdict: "reject", by: marks.some((m) => m.by === "you") ? "you" : "app" };
  }
  return { verdict: "open", by: null };
}

export function inFilter(photo: Photo, filter: Filter): boolean {
  if (filter === "open") return markOf(photo).verdict === "open";
  return photo.pile === (filter === "app_reject" ? "auto_reject" : "auto_keep");
}

export function matches(tile: Tile, filter: Filter): boolean {
  return tile.frames.some((p) => inFilter(p, filter));
}

const PANEL_KEY = "raw-image-editor.panel";

function storedPanel(): boolean {
  try {
    return localStorage.getItem(PANEL_KEY) !== "hidden";
  } catch {
    return true; // storage blocked (a private window): the panel shows
  }
}

export function setPanel(visible: boolean): void {
  ui.panel = visible;
  try {
    localStorage.setItem(PANEL_KEY, visible ? "shown" : "hidden");
  } catch {
    // not remembered; it still applies until the page reloads
  }
}

export const ui = $state({
  view: "folders" as "folders" | "cull",
  mode: "grid" as "grid" | "loupe" | "compare",
  folderId: null as number | null,
  folder: null as FolderDetail | null,
  photos: [] as Photo[],
  filter: null as Filter | null, // null: every photo
  currentId: null as number | null,
  compareFrom: "grid" as "grid" | "loupe", // where Esc and C in burst compare go back to
  heat: false,
  zoom: false,
  help: false,
  debug: false,
  done: false,
  status: null as Status | null,
  jobs: [] as Job[],
  message: "",
  reasonFor: null as number | null,
  offline: false, // the server (`uv run rie serve`) cannot be reached
  panel: storedPanel(), // the action panel on the right
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
  const another = id !== ui.folderId;
  ui.folderId = id;
  ui.folder = folder;
  ui.photos = photos;
  ui.view = "cull";
  // Another folder opens on everything, in the grid, at the first photo or burst still to decide; so does this one
  // when the selected photo has gone (to the trash at Done culling, for example), but with its filter kept.
  if (another) {
    ui.filter = null;
    ui.mode = "grid";
  }
  if (another || !photos.some((p) => p.id === ui.currentId && p.state === "present")) ui.currentId = firstOpen();
}

// The photo the folder opens on: the cover of its first open tile, else of its first tile.
export function firstOpen(): number | null {
  const tiles = tilesOf(ui.photos.filter((p) => p.state === "present"));
  return (tiles.find((t) => t.mark.verdict === "open") ?? tiles[0])?.photo.id ?? null;
}

export async function refreshFolder(): Promise<void> {
  if (ui.folderId !== null) await loadFolder(ui.folderId);
}

// One tile per single photo, and one per burst holding all of its frames whatever their piles.
export interface Tile {
  key: string; // "b<burst id>" or "p<photo id>"
  photo: Photo; // the cover: a burst's kept frame (the best-ranked kept one), else its best frame
  frames: Photo[]; // in shooting order; one for a single photo
  scene: number | null;
  mark: Mark;
}

export function tilesOf(items: Photo[]): Tile[] {
  const byKey = new Map<string, Photo[]>();
  for (const p of items) {
    const key = p.burst.id !== null && p.burst.size > 1 ? `b${p.burst.id}` : `p${p.id}`;
    const list = byKey.get(key);
    if (list) list.push(p);
    else byKey.set(key, [p]);
  }
  return [...byKey.entries()].map(([key, frames]) => {
    frames.sort((a, b) => a.burst.position - b.burst.position);
    const byRank = (list: Photo[]) => list.reduce((best, p) => (p.burst.rank < best.burst.rank ? p : best), list[0]);
    const kept = frames.filter((p) => markOf(p).verdict === "keep");
    return {
      key,
      photo: kept.length ? byRank(kept) : byRank(frames),
      frames,
      scene: frames[0].scene,
      mark: frames.length > 1 ? burstMark(frames) : markOf(frames[0]),
    };
  });
}

// Consecutive tiles of one scene, in order.
export interface SceneGroup {
  scene: number | null;
  tiles: Tile[];
}

export function scenesOf(tiles: Tile[]): SceneGroup[] {
  const out: SceneGroup[] = [];
  for (const tile of tiles) {
    const last = out.at(-1);
    if (last && last.scene === tile.scene) last.tiles.push(tile);
    else out.push({ scene: tile.scene, tiles: [tile] });
  }
  return out;
}

// "#2 kept by you; #1 rejected by the app; #3, #4 rejected by you": every frame's mark, keeps first.
export function burstLine(frames: Photo[]): string {
  const groups = new Map<string, { verdict: Verdict; numbers: number[] }>();
  for (const frame of frames) {
    const mark = markOf(frame);
    const text = markText(mark);
    const group = groups.get(text);
    if (group) group.numbers.push(frame.burst.position);
    else groups.set(text, { verdict: mark.verdict, numbers: [frame.burst.position] });
  }
  const order: Verdict[] = ["keep", "open", "reject"];
  return [...groups.entries()]
    .sort(([, a], [, b]) => order.indexOf(a.verdict) - order.indexOf(b.verdict) || a.numbers[0] - b.numbers[0])
    .map(([text, g]) => `${g.numbers.map((n) => `#${n}`).join(", ")} ${text}`)
    .join("; ");
}

// "1 keep · 2 reject · 3 open"
export function burstCounts(frames: Photo[]): string {
  const marks = frames.map(markOf);
  const parts: [number, string][] = [
    [marks.filter((m) => m.verdict === "keep").length, "keep"],
    [marks.filter((m) => m.verdict === "reject").length, "reject"],
    [marks.filter((m) => m.verdict === "open").length, "open"],
  ];
  return parts
    .filter(([n]) => n > 0)
    .map(([n, word]) => `${n} ${word}`)
    .join(" · ");
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
