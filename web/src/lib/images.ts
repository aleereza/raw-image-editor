// Decoded-image cache and timing. Flipping is fast when the next image is already downloaded and decoded:
// the loupe shows the cached <img> element itself, so the browser only has to paint it.

// Screen-size copies are about 3 MB decoded; full-size JPEGs about 96 MB, so only the last few are kept.
const MAX_SCREEN = 24;
const MAX_FULL = 3;
const screenCache = new Map<string, { img: HTMLImageElement; ready: Promise<HTMLImageElement> }>();
const fullCache = new Map<string, { img: HTMLImageElement; ready: Promise<HTMLImageElement> }>();

export function load(url: string): { img: HTMLImageElement; ready: Promise<HTMLImageElement> } {
  const full = url.includes("/full");
  const cache = full ? fullCache : screenCache;
  const max = full ? MAX_FULL : MAX_SCREEN;
  const hit = cache.get(url);
  if (hit) {
    cache.delete(url);
    cache.set(url, hit); // most recently used last
    return hit;
  }
  const img = new Image();
  img.decoding = "async";
  img.src = url;
  const ready = img.decode().then(
    () => img,
    () => img, // a failed decode still resolves; the element shows the browser's broken-image state
  );
  const entry = { img, ready };
  cache.set(url, entry);
  while (cache.size > max) {
    const oldest = cache.keys().next().value;
    if (oldest === undefined) break;
    cache.delete(oldest);
  }
  return entry;
}

export function isDecoded(url: string): boolean {
  const hit = (url.includes("/full") ? fullCache : screenCache).get(url);
  return !!hit && hit.img.complete && hit.img.naturalWidth > 0;
}

// Timings of the last flips and zooms, from the key press to the frame after the new image was put on screen.
export interface Timing {
  kind: "flip" | "zoom";
  ms: number;
  cached: boolean;
}
export const timings: Timing[] = [];
declare global {
  interface Window {
    __rie?: { timings: Timing[]; clear: () => void };
  }
}
// `clear` empties the decoded-image cache, so a measurement can start cold.
window.__rie = {
  timings,
  clear: () => {
    screenCache.clear();
    fullCache.clear();
  },
};

let pending: { kind: "flip" | "zoom"; start: number; cached: boolean } | null = null;

export function startTiming(kind: "flip" | "zoom", cached: boolean): void {
  pending = { kind, start: performance.now(), cached };
}

export function stopTiming(): void {
  const current = pending;
  if (!current) return;
  pending = null;
  // Two animation frames: the first runs before the paint that shows the new image, the second after it.
  requestAnimationFrame(() =>
    requestAnimationFrame(() => {
      timings.push({ kind: current.kind, ms: Math.round((performance.now() - current.start) * 10) / 10, cached: current.cached });
      if (timings.length > 200) timings.shift();
    }),
  );
}
