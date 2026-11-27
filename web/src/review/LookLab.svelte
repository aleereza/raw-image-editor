<script lang="ts">
  // The look lab (plan §4.12): variations of the current look side by side, like an eye test. Picking one starts
  // the next round around it with smaller steps; the favourite can be saved as a named look and used for the photo
  // or its whole scene.
  import { reviewApi, reviewUrls, type Edit, type LookDef, type Variation } from "./api";

  let { photo, looks, onclose, onchanged }: {
    photo: Edit;
    looks: LookDef[];
    onclose: () => void;
    onchanged: (message: string) => void;
  } = $props();

  let round = $state(1);
  let base = $state<LookDef | null>(null);
  let variations = $state<Variation[]>([]);
  let busy = $state(false);
  let error = $state("");
  let name = $state("");

  async function load(look: LookDef | null, r: number) {
    busy = true;
    error = "";
    try {
      const result = await reviewApi.lookLab(photo.id, look, r);
      variations = result.variations;
      round = r;
      base = result.variations[0]?.look ?? look;
    } catch (e) {
      error = (e as Error).message;
    } finally {
      busy = false;
    }
  }

  function pick(v: Variation) {
    load(v.look, round + 1);
  }

  async function save(useFor: "photo" | "scene" | null) {
    if (!base) return;
    busy = true;
    error = "";
    try {
      const saved = await reviewApi.saveLook(name || "my-look", base, "");
      looks.push(saved);
      if (useFor) {
        const result = await reviewApi.useLook(photo.id, saved.id, useFor === "scene");
        onchanged(`${saved.id} on ${result.changed.length || 1} photo(s)`);
      } else {
        onchanged(`saved ${saved.id}`);
      }
    } catch (e) {
      error = (e as Error).message;
    } finally {
      busy = false;
    }
  }

  async function use(look: string, scene: boolean) {
    busy = true;
    try {
      const result = await reviewApi.useLook(photo.id, look, scene);
      onchanged(`${look} on ${result.changed.length || 1} photo(s)`);
    } catch (e) {
      error = (e as Error).message;
    } finally {
      busy = false;
    }
  }

  $effect(() => {
    load(null, 1);
  });
</script>

<div class="lab" role="dialog" aria-label="Look lab">
  <header>
    <h2>Look lab · {photo.name} · round {round}</h2>
    <span class="muted">Pick the one you like best; the next round narrows in around it.</span>
    <button onclick={onclose}>Close <kbd>Esc</kbd></button>
  </header>
  {#if error}<p class="error">{error}</p>{/if}
  {#if busy && !variations.length}<p class="muted">Rendering the variations…</p>{/if}
  <div class="grid" class:busy>
    {#each variations as v (v.key)}
      <button class="variation" onclick={() => pick(v)} disabled={busy} title="Start the next round from this one">
        <img src={reviewUrls.variation(v)} alt={v.label} />
        <span>{v.label}</span>
        <small class="muted">contrast {v.look.contrast} · warmth {v.look.warmth} · saturation {v.look.saturation}</small>
      </button>
    {/each}
  </div>
  <footer>
    <label>Save as <input type="text" bind:value={name} placeholder="indoor-family" /></label>
    <button disabled={busy || !base} onclick={() => save(null)}>Save</button>
    <button disabled={busy || !base} onclick={() => save("photo")}>Save and use for this photo</button>
    <button class="primary" disabled={busy || !base} onclick={() => save("scene")}>Save and use for the scene</button>
    <span class="sep"></span>
    <span class="muted">Saved looks:</span>
    {#each looks as look (look.id)}
      <button disabled={busy} onclick={() => use(look.id, true)} title="Use for this photo's whole scene">{look.id}</button>
    {/each}
  </footer>
</div>

<style>
  .lab { position: fixed; inset: 0; background: var(--bg); z-index: 30; display: flex; flex-direction: column; padding: 12px; gap: 8px; }
  header, footer { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  h2 { font-size: 16px; margin: 0; }
  .grid { flex: 1; overflow: auto; display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 10px; align-content: start; }
  .grid.busy { opacity: 0.5; }
  .variation { display: flex; flex-direction: column; gap: 4px; padding: 6px; text-align: left; }
  .variation img { width: 100%; height: auto; border-radius: 4px; }
  .error { color: var(--reject); }
  .sep { flex: 1; }
</style>
