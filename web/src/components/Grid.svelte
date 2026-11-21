<script lang="ts">
  import { urls } from "../lib/api";
  import { burstCounts, markOf, scenesOf, ui, type Tile } from "../lib/state.svelte";
  import Badges from "./Badges.svelte";
  import Mark from "./Mark.svelte";

  let {
    tiles,
    numbers,
    onselect,
    onopen,
  }: {
    tiles: Tile[];
    numbers: Map<number | null, number>; // each scene's number among all of the folder's scenes
    onselect: (id: number) => void;
    onopen: (tile: Tile, id: number) => void; // on a double-click: a single photo's loupe, a burst's compare
  } = $props();
  const size = 210;
  const DOTS = 8; // bigger bursts show their counts only

  const scenes = $derived(scenesOf(tiles));
  const order = $derived(new Map(tiles.map((t, i) => [t.key, i])));

  $effect(() => {
    const id = ui.currentId;
    const node = document.querySelector(`[data-photo="${id}"]`);
    node?.scrollIntoView({ block: "nearest" });
  });
</script>

<div class="grid">
  {#each scenes as scene, s (s)}
    {@const photos = scene.tiles.reduce((n, t) => n + t.frames.length, 0)}
    {@const open = scene.tiles.filter((t) => t.mark.verdict === "open").length}
    <div class="scene">
      <div class="scene-head muted">
        Scene {numbers.get(scene.scene) ?? s + 1} · {photos} {photos === 1 ? "photo" : "photos"}{open ? ` · ${open} to decide` : ""}
      </div>
      <div class="tiles">
        {#each scene.tiles as tile (tile.key)}
          {@const burst = tile.frames.length > 1}
          {@const p = tile.frames.find((f) => f.id === ui.currentId) ?? tile.photo}
          <button
            class="tile"
            class:active={p.id === ui.currentId}
            class:burst
            class:rejected={tile.mark.verdict === "reject"}
            data-tile={tile.key}
            data-photo={p.id}
            style:width="{size}px"
            onclick={() => onselect(p.id)}
            ondblclick={() => onopen(tile, p.id)}
            title={p.reason ?? p.name}
          >
            <img src={urls.thumb(p)} alt={p.name} loading={(order.get(tile.key) ?? 0) < 60 ? "eager" : "lazy"} decoding="async" />
            <span class="corner"><Mark mark={tile.mark} /></span>
            <Badges photo={p} />
            {#if burst}
              <span class="dots" title="{tile.frames.length} frames: {burstCounts(tile.frames)}">
                {#if tile.frames.length <= DOTS}
                  {#each tile.frames as frame (frame.id)}
                    {@const m = markOf(frame)}
                    <span class="dot {m.verdict} {m.by ?? ''}" class:shown={frame.id === p.id}></span>
                  {/each}
                {/if}
                <span class="counts">{burstCounts(tile.frames)}</span>
              </span>
            {/if}
          </button>
        {/each}
      </div>
    </div>
  {/each}
</div>

<style>
  .grid { height: 100%; overflow-y: auto; padding: 8px 8px 16px; }
  .scene { margin-bottom: 12px; }
  .scene-head { font-size: 12px; margin: 4px 2px 0; }
  .tiles { display: flex; flex-wrap: wrap; gap: 14px 8px; padding-top: 12px; }
  .tile {
    position: relative; height: 160px; padding: 0; border: 2px solid transparent; border-radius: 6px;
    background: #000; display: flex; align-items: center; justify-content: center; scroll-margin: 36px 0 12px;
  }
  .tile img { max-width: 100%; max-height: 100%; object-fit: contain; display: block; border-radius: 4px; }
  .tile.active { border-color: var(--accent); }
  .tile.rejected img { opacity: 0.4; }
  /* A burst: the edges of two more photos behind it. */
  .tile.burst::before, .tile.burst::after {
    content: ""; position: absolute; border: 1px solid #5a5a66; border-bottom: none; border-radius: 6px 6px 0 0;
    pointer-events: none;
  }
  .tile.burst::before { left: 6px; right: 6px; top: -7px; height: 4px; }
  .tile.burst::after { left: 13px; right: 13px; top: -11px; height: 4px; }
  .corner { position: absolute; top: 5px; left: 5px; display: flex; pointer-events: none; }
  .dots {
    position: absolute; top: 5px; right: 5px; max-width: calc(100% - 34px); display: flex; flex-wrap: wrap;
    justify-content: flex-end; align-items: center; gap: 3px; padding: 2px 7px; border-radius: 9px;
    background: rgba(0, 0, 0, 0.62); color: #e6e6ea; font-size: 11px; line-height: 14px;
  }
  .dot { width: 7px; height: 7px; flex: none; border-radius: 50%; box-sizing: border-box; border: 1.5px solid #c4c4cc; }
  .dot.keep.you { background: var(--keep); border-color: var(--keep); }
  .dot.reject.you { background: var(--reject); border-color: var(--reject); }
  .dot.keep.app { border-color: var(--keep); }
  .dot.reject.app { border-color: var(--reject); }
  .dot.shown { outline: 1px solid #fff; outline-offset: 1px; }
  .counts { margin-left: 3px; white-space: nowrap; }
</style>
