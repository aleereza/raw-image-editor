<script lang="ts">
  import { untrack } from "svelte";
  import { urls, type Analysis, type Photo } from "../lib/api";
  import { load, stopTiming } from "../lib/images";
  import { analysisFor, ui } from "../lib/state.svelte";
  import { blurColour } from "../lib/focus";

  let { photo, items }: { photo: Photo; items: Photo[] } = $props();

  let stage: HTMLDivElement | undefined = $state();
  let canvas: HTMLCanvasElement | undefined = $state();
  let width = $state(0);
  let height = $state(0);
  let analysis = $state<Analysis | null>(null);
  // What the stage shows: the <img>, whose photo, whether it is the full-size copy, and its size in pixels.
  let shown = $state.raw<{ img: HTMLImageElement; id: number; zoomed: boolean; w: number; h: number } | null>(null);
  let offset = $state({ x: 0, y: 0 }); // zoom: where the image's top-left sits in the stage
  let loading = $state(false);
  let dragging = $state(false);

  const PREFETCH = 5;
  // Changes only when another photo comes on screen, not when the photo list is refreshed.
  const photoId = $derived(photo.id);

  function show(img: HTMLImageElement, id: number, zoomed: boolean) {
    if (!stage) return;
    img.className = zoomed ? "zoomed" : "fit";
    img.draggable = false;
    const old = stage.querySelector("img");
    if (old !== img) {
      if (old) old.remove();
      stage.prepend(img);
    }
    // The same image shown again (the list refreshed) changes nothing, so a zoomed view keeps its place.
    if (!(shown && shown.img === img && shown.id === id && shown.zoomed === zoomed)) {
      shown = { img, id, zoomed, w: img.naturalWidth, h: img.naturalHeight };
    }
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

  // Prefetch and decode the neighbours.
  $effect(() => {
    const list = items;
    const i = list.findIndex((x) => x.id === photoId);
    if (i < 0) return;
    for (let d = 1; d <= PREFETCH; d += 1) {
      if (list[i + d]) load(urls.screen(list[i + d]));
      if (list[i - d]) load(urls.screen(list[i - d]));
    }
  });

  // After a short pause on a photo, fetch its full size and its analysis.
  $effect(() => {
    const id = photoId;
    const full = untrack(() => urls.full(photo));
    analysis = null;
    const timer = setTimeout(() => {
      load(full);
      analysisFor(id).then((a) => {
        if (photoId === id) analysis = a;
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

  // The full-size copy's size on screen at 100 %: one image pixel per device pixel.
  const zoomedSize = $derived.by(() => {
    if (!shown?.zoomed) return null;
    const dpr = window.devicePixelRatio || 1;
    return { w: shown.w / dpr, h: shown.h / dpr };
  });
  // Zoomed in on this photo, with its full-size copy on screen.
  const zoomedNow = $derived(ui.zoom && zoomedSize !== null && shown?.id === photoId);

  // Keep the zoomed image over the stage: centred along a side where it is smaller, never moved past its edges.
  function clamp(x: number, y: number): { x: number; y: number } {
    if (!zoomedSize) return { x, y };
    const along = (value: number, size: number, view: number) =>
      size <= view ? (view - size) / 2 : Math.min(0, Math.max(view - size, value));
    return { x: along(x, zoomedSize.w, width), y: along(y, zoomedSize.h, height) };
  }

  // Zooming in centres on the eyes; when the analysis arrives a moment later, it centres again.
  $effect(() => {
    if (!zoomedNow || !zoomedSize) return;
    const [u, v] = focusPoint(analysis);
    offset = clamp(width / 2 - u * zoomedSize.w, height / 2 - v * zoomedSize.h);
  });

  // Size and place the image: the full-size copy at 100 % where `offset` says, the screen copy fitted by CSS.
  // Everything it depends on is read before anything else, so it follows every change.
  $effect(() => {
    const s = shown;
    const size = zoomedSize;
    const { x, y } = offset;
    if (!s) return;
    s.img.style.width = size ? `${size.w}px` : "";
    s.img.style.transform = size ? `translate(${x}px, ${y}px)` : "";
  });

  // Move the zoomed image by dragging it, or by scrolling (a mouse wheel, or two fingers on a touchpad).
  let drag: { x: number; y: number; ox: number; oy: number } | null = null;
  function down(event: PointerEvent) {
    if (!zoomedNow || event.button !== 0) return;
    drag = { x: event.clientX, y: event.clientY, ox: offset.x, oy: offset.y };
    dragging = true;
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  }
  function move(event: PointerEvent) {
    if (!drag) return;
    offset = clamp(drag.ox + event.clientX - drag.x, drag.oy + event.clientY - drag.y);
  }
  function up() {
    drag = null;
    dragging = false;
  }
  $effect(() => {
    const el = stage;
    if (!el) return;
    const wheel = (event: WheelEvent) => {
      if (!zoomedNow || event.ctrlKey) return; // Ctrl+scroll and pinching stay the browser's own zoom
      event.preventDefault();
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? el.clientHeight : 1; // lines, pages, pixels
      offset = clamp(offset.x - event.deltaX * unit, offset.y - event.deltaY * unit);
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  });

  // The displayed image's rectangle when fitted, for the heatmap.
  const fit = $derived.by(() => {
    if (!shown || shown.zoomed || !shown.w || !width || !height) return null;
    const scale = Math.min(width / shown.w, height / shown.h, 1); // as object-fit: contain, never enlarged
    const w = shown.w * scale;
    const h = shown.h * scale;
    return { x: (width - w) / 2, y: (height - h) / 2, w, h };
  });

  $effect(() => {
    if (!canvas || !fit) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(fit.w * dpr);
    canvas.height = Math.round(fit.h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, fit.w, fit.h);
    if (!ui.heat || !analysis || shown?.id !== photoId) return;
    const heat = analysis.heat;
    if (heat) {
      const cw = fit.w / heat.cols;
      const ch = fit.h / heat.rows;
      for (let r = 0; r < heat.rows; r += 1) {
        for (let c = 0; c < heat.cols; c += 1) {
          const value = heat.blur[r * heat.cols + c];
          if (value === null) continue;
          ctx.fillStyle = blurColour(value);
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
  class:zoom={shown?.zoomed ?? false}
  class:dragging
  bind:this={stage}
  bind:clientWidth={width}
  bind:clientHeight={height}
  onpointerdown={down}
  onpointermove={move}
  onpointerup={up}
  onpointercancel={up}
  role="presentation"
>
  {#if fit && !ui.zoom}
    <canvas bind:this={canvas} style:left="{fit.x}px" style:top="{fit.y}px" style:width="{fit.w}px" style:height="{fit.h}px"></canvas>
  {/if}
  {#if loading}<div class="loading muted">{ui.zoom ? "loading the full-size JPEG…" : "loading…"}</div>{/if}
  {#if ui.heat && !ui.zoom}
    <div class="legend">
      sharp <span style:background={blurColour(0.3)}></span><span style:background={blurColour(1)}></span><span style:background={blurColour(2)}></span> soft
      {#if analysis?.focus.reason}<span class="muted"> · {analysis.focus.reason}</span>{/if}
    </div>
  {/if}
  {#if ui.zoom}<div class="legend">100 % · drag or scroll to move around · <kbd>Z</kbd> back</div>{/if}
</div>

<style>
  .stage { position: relative; width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; overflow: hidden; background: #0a0a0b; }
  .stage.zoom { display: block; cursor: grab; }
  .stage.zoom.dragging { cursor: grabbing; }
  .stage :global(img.fit) { max-width: 100%; max-height: 100%; object-fit: contain; display: block; user-select: none; }
  .stage :global(img.zoomed) { position: absolute; left: 0; top: 0; max-width: none; max-height: none; user-select: none; }
  canvas { position: absolute; pointer-events: none; }
  .loading { position: absolute; top: 12px; left: 12px; }
  .legend { position: absolute; bottom: 10px; left: 10px; background: #000b; padding: 4px 8px; border-radius: 4px; font-size: 12px; display: flex; gap: 4px; align-items: center; }
  .legend span[style] { width: 18px; height: 10px; display: inline-block; }
</style>
