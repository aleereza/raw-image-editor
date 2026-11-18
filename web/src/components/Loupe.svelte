<script lang="ts">
  import { untrack } from "svelte";
  import { urls, type Analysis, type Photo } from "../lib/api";
  import { load, stopTiming } from "../lib/images";
  import { analysisFor, ui } from "../lib/state.svelte";

  let { photo, items }: { photo: Photo; items: Photo[] } = $props();

  let stage: HTMLDivElement | undefined = $state();
  let canvas: HTMLCanvasElement | undefined = $state();
  let width = $state(0);
  let height = $state(0);
  let analysis = $state<Analysis | null>(null);
  let shownId = $state<number | null>(null);
  let natural = $state({ w: 0, h: 0 });
  let offset = $state({ x: 0, y: 0 }); // zoom: where the image's top-left sits in the stage
  let loading = $state(false);

  const PREFETCH = 5;

  function show(img: HTMLImageElement, id: number, zoomed: boolean) {
    if (!stage) return;
    img.className = zoomed ? "zoomed" : "fit";
    img.draggable = false;
    const old = stage.querySelector("img");
    if (old !== img) {
      if (old) old.remove();
      stage.prepend(img);
    }
    shownId = id;
    natural = { w: img.naturalWidth, h: img.naturalHeight };
    loading = false;
    stopTiming();
  }

  // Show the current photo: the cached, decoded <img> element goes on the stage as it is.
  $effect(() => {
    const p = photo;
    const zoomed = ui.zoom;
    const entry = load(zoomed ? urls.full(p) : urls.screen(p));
    untrack(() => {
      if (entry.img.complete && entry.img.naturalWidth > 0) show(entry.img, p.id, zoomed);
      else {
        loading = true;
        entry.ready.then(() => {
          if (photo.id === p.id && ui.zoom === zoomed) show(entry.img, p.id, zoomed);
        });
      }
    });
  });

  // Prefetch and decode the neighbours; after a short pause, the full size and the analysis of this photo too.
  $effect(() => {
    const p = photo;
    const list = items;
    const i = list.findIndex((x) => x.id === p.id);
    if (i >= 0) {
      for (let d = 1; d <= PREFETCH; d += 1) {
        if (list[i + d]) load(urls.screen(list[i + d]));
        if (list[i - d]) load(urls.screen(list[i - d]));
      }
    }
    analysis = null;
    const timer = setTimeout(() => {
      load(urls.full(p));
      analysisFor(p.id).then((a) => {
        if (photo.id === p.id) analysis = a;
      });
    }, 300);
    return () => clearTimeout(timer);
  });

  // Where the zoomed image sits: centred on the eyes, else the AF point, else the middle.
  function focusPoint(a: Analysis | null): [number, number] {
    const eyes = a?.faces.flatMap((f) => f.eyes) ?? [];
    const boxes = a?.focus.subject_boxes ?? [];
    if (a?.focus.subject === "eyes" && boxes.length) {
      const [x, y, w, h] = boxes[0];
      const inside = eyes.filter(([ex, ey]) => ex >= x && ex <= x + w && ey >= y && ey <= y + h);
      if (inside.length) return [inside.reduce((s, e) => s + e[0], 0) / inside.length, inside.reduce((s, e) => s + e[1], 0) / inside.length];
    }
    if (boxes.length) {
      const [x, y, w, h] = boxes[0];
      return [x + w / 2, y + h / 2];
    }
    const af = a?.focus.af_box;
    if (af) return [af[0] + af[2] / 2, af[1] + af[3] / 2];
    return [0.5, 0.5];
  }

  $effect(() => {
    if (!ui.zoom || shownId !== photo.id || !natural.w) return;
    const dpr = window.devicePixelRatio || 1;
    const w = natural.w / dpr;
    const h = natural.h / dpr;
    const [u, v] = focusPoint(analysis);
    const clamp = (value: number, size: number, view: number) => (size <= view ? (view - size) / 2 : Math.min(0, Math.max(view - size, value)));
    offset = { x: clamp(width / 2 - u * w, w, width), y: clamp(height / 2 - v * h, h, height) };
  });

  $effect(() => {
    const img = stage?.querySelector("img");
    if (!img) return;
    if (ui.zoom) {
      const dpr = window.devicePixelRatio || 1;
      img.style.width = `${natural.w / dpr}px`;
      img.style.transform = `translate(${offset.x}px, ${offset.y}px)`;
    } else {
      img.style.width = "";
      img.style.transform = "";
    }
  });

  // Drag to pan at 100%.
  let drag: { x: number; y: number; ox: number; oy: number } | null = null;
  function down(event: PointerEvent) {
    if (!ui.zoom) return;
    drag = { x: event.clientX, y: event.clientY, ox: offset.x, oy: offset.y };
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  }
  function move(event: PointerEvent) {
    if (!drag) return;
    offset = { x: drag.ox + event.clientX - drag.x, y: drag.oy + event.clientY - drag.y };
  }
  function up() {
    drag = null;
  }

  // The displayed image's rectangle when fitted, for the heatmap.
  const fit = $derived.by(() => {
    if (!natural.w || !width || !height) return null;
    const scale = Math.min(width / natural.w, height / natural.h, 1); // as object-fit: contain, never enlarged
    const w = natural.w * scale;
    const h = natural.h * scale;
    return { x: (width - w) / 2, y: (height - h) / 2, w, h };
  });

  function colour(blur: number): string {
    // green at 0.4 or less, yellow around 1, red from 1.8 (ADR 0008's provisional thresholds)
    const t = Math.min(Math.max((blur - 0.4) / 1.4, 0), 1);
    const r = Math.round(t < 0.5 ? 80 + t * 2 * 175 : 255);
    const g = Math.round(t < 0.5 ? 220 : 220 - (t - 0.5) * 2 * 190);
    return `rgba(${r}, ${g}, 60, 0.45)`;
  }

  $effect(() => {
    if (!canvas || !fit) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(fit.w * dpr);
    canvas.height = Math.round(fit.h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, fit.w, fit.h);
    if (!ui.heat || !analysis || shownId !== photo.id) return;
    const heat = analysis.heat;
    if (heat) {
      const cw = fit.w / heat.cols;
      const ch = fit.h / heat.rows;
      for (let r = 0; r < heat.rows; r += 1) {
        for (let c = 0; c < heat.cols; c += 1) {
          const value = heat.blur[r * heat.cols + c];
          if (value === null) continue;
          ctx.fillStyle = colour(value);
          ctx.fillRect(c * cw, r * ch, Math.ceil(cw), Math.ceil(ch));
        }
      }
    }
    ctx.lineWidth = 1.5;
    for (const face of analysis.faces) {
      const [x, y, w, h] = face.box;
      ctx.strokeStyle = face.blink !== null && face.blink > 0.5 ? "#e3b341" : "#6aa2ff";
      ctx.strokeRect(x * fit.w, y * fit.h, w * fit.w, h * fit.h);
      for (const [ex, ey] of face.eyes) {
        ctx.beginPath();
        ctx.arc(ex * fit.w, ey * fit.h, 2.5, 0, Math.PI * 2);
        ctx.fillStyle = "#ff5cf0";
        ctx.fill();
      }
    }
    const af = analysis.focus.af_box;
    if (af) {
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = "#ffffff";
      ctx.strokeRect(af[0] * fit.w, af[1] * fit.h, af[2] * fit.w, af[3] * fit.h);
      ctx.setLineDash([]);
    }
  });
</script>

<div
  class="stage"
  class:zoom={ui.zoom}
  bind:this={stage}
  bind:clientWidth={width}
  bind:clientHeight={height}
  onpointerdown={down}
  onpointermove={move}
  onpointerup={up}
  role="presentation"
>
  {#if fit && !ui.zoom}
    <canvas bind:this={canvas} style:left="{fit.x}px" style:top="{fit.y}px" style:width="{fit.w}px" style:height="{fit.h}px"></canvas>
  {/if}
  {#if loading}<div class="loading muted">{ui.zoom ? "loading the full-size JPEG…" : "loading…"}</div>{/if}
  {#if ui.heat && !ui.zoom}
    <div class="legend">
      sharp <span style:background={colour(0.3)}></span><span style:background={colour(1)}></span><span style:background={colour(2)}></span> soft
      {#if analysis?.focus.reason}<span class="muted"> · {analysis.focus.reason}</span>{/if}
    </div>
  {/if}
  {#if ui.zoom}<div class="legend">100% · drag to pan · <kbd>Z</kbd> back</div>{/if}
</div>

<style>
  .stage { position: relative; width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; overflow: hidden; background: #0a0a0b; }
  .stage.zoom { display: block; cursor: grab; }
  .stage :global(img.fit) { max-width: 100%; max-height: 100%; object-fit: contain; display: block; user-select: none; }
  .stage :global(img.zoomed) { position: absolute; left: 0; top: 0; max-width: none; max-height: none; user-select: none; }
  canvas { position: absolute; pointer-events: none; }
  .loading { position: absolute; top: 12px; left: 12px; }
  .legend { position: absolute; bottom: 10px; left: 10px; background: #000b; padding: 4px 8px; border-radius: 4px; font-size: 12px; display: flex; gap: 4px; align-items: center; }
  .legend span[style] { width: 18px; height: 10px; display: inline-block; }
</style>
