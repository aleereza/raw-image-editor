<script lang="ts">
  import { urls } from "../lib/api";
  import { ui, type Tile } from "../lib/state.svelte";
  import Badges from "./Badges.svelte";

  let { tiles, columns = $bindable(6), onopen }: { tiles: Tile[]; columns?: number; onopen: (id: number) => void } = $props();
  let width = $state(1200);
  const size = 210;
  $effect(() => {
    columns = Math.max(1, Math.floor((width - 16) / (size + 8)));
  });

  // Group tiles by scene, keeping their order.
  const scenes = $derived.by(() => {
    const out: { scene: number | null; tiles: Tile[] }[] = [];
    for (const tile of tiles) {
      const last = out.at(-1);
      if (last && last.scene === tile.photo.scene) last.tiles.push(tile);
      else out.push({ scene: tile.photo.scene, tiles: [tile] });
    }
    return out;
  });

  $effect(() => {
    const id = ui.currentId;
    const node = document.querySelector(`[data-photo="${id}"]`);
    node?.scrollIntoView({ block: "nearest" });
  });
</script>

<div class="grid" bind:clientWidth={width}>
  {#each scenes as scene, s (s)}
    <div class="scene">
      <div class="scene-head muted">Scene {s + 1} · {scene.tiles.reduce((n, t) => n + t.frames.length, 0)} photos</div>
      <div class="tiles">
        {#each scene.tiles as tile (tile.photo.id)}
          {@const p = tile.frames.find((f) => f.id === ui.currentId) ?? tile.photo}
          {@const active = p.id === ui.currentId}
          <button
            class="tile"
            class:active
            class:keep={p.decision === "keep"}
            class:reject={p.decision === "reject"}
            data-photo={p.id}
            style:width="{size}px"
            onclick={() => (ui.currentId = p.id)}
            ondblclick={() => onopen(p.id)}
            title={p.reason ?? p.name}
          >
            <img src={urls.thumb(p)} alt={p.name} loading={tiles.indexOf(tile) < 60 ? "eager" : "lazy"} decoding="async" />
            <Badges photo={p} />
            {#if tile.frames.length > 1}<span class="stack">×{tile.frames.length}</span>{/if}
          </button>
        {/each}
      </div>
    </div>
  {/each}
</div>

<style>
  .grid { height: 100%; overflow-y: auto; padding: 8px; }
  .scene { margin-bottom: 14px; }
  .scene-head { font-size: 12px; margin: 4px 2px 6px; }
  .tiles { display: flex; flex-wrap: wrap; gap: 8px; }
  .tile {
    position: relative; height: 160px; padding: 0; border: 2px solid transparent; border-radius: 6px; overflow: hidden;
    background: #000; display: flex; align-items: center; justify-content: center;
  }
  .tile img { max-width: 100%; max-height: 100%; object-fit: contain; display: block; }
  .tile.active { border-color: var(--accent); }
  .tile.keep { box-shadow: inset 0 -4px 0 var(--keep); }
  .tile.reject img { opacity: 0.45; }
  .stack {
    position: absolute; top: 4px; right: 4px; background: var(--accent); color: #0b1020; font-weight: 700;
    border-radius: 10px; padding: 0 7px; font-size: 12px;
  }
</style>
