<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../lib/api";
  import { refreshFolder, say, ui } from "../lib/state.svelte";

  let { folderId, onclose }: { folderId: number; onclose: () => void } = $props();
  type Preview = Awaited<ReturnType<typeof api.donePreview>>;
  type Result = Awaited<ReturnType<typeof api.done>>;
  let preview = $state<Preview | null>(null);
  let result = $state<Result | null>(null);
  let error = $state("");
  let busy = $state(false);

  onMount(async () => {
    try {
      preview = await api.donePreview(folderId);
    } catch (e) {
      error = (e as Error).message;
    }
  });

  async function confirm() {
    busy = true;
    try {
      result = await api.done(folderId);
      await refreshFolder();
      ui.mode = "grid";
    } catch (e) {
      error = (e as Error).message;
    } finally {
      busy = false;
    }
  }

  function close() {
    if (result) say(`${result.trashed} rejects moved to the drive's trash`);
    onclose();
  }
</script>

<div class="overlay" role="presentation">
  <div class="dialog" role="dialog" aria-label="Done culling">
    <h2>Done culling</h2>
    {#if error}
      <p class="error">{error}</p>
    {:else if result}
      <p>Moved <b>{result.trashed}</b> rejects ({result.files} files, {result.size}) to the drive's trash. {result.kept} photos are kept.</p>
      {#if result.errors.length}<p class="error">Not moved: {result.errors.join("; ")}</p>{/if}
      {#if result.model}<p class="muted">Personal model: {result.model.promoted ? "updated" : "not updated"} — {result.model.note}</p>{/if}
      <p class="muted">The folders page can restore them; your file manager's Trash can too.</p>
    {:else if preview}
      <p><b>{preview.rejects}</b> rejects ({preview.files} files with their camera JPEGs and sidecars, {preview.size}) will move to the drive's trash, which only you empty.</p>
      <p>{preview.keeps} photos are kept{preview.undecided ? `, including ${preview.undecided} still to decide` : ""}. {preview.auto} were decided by the app on its own.</p>
      {#if preview.open_bursts}
        <p class="muted">
          {preview.open_bursts === 1 ? "1 burst has" : `${preview.open_bursts} bursts have`} no keep yet; {preview.open_bursts === 1 ? "its" : "their"} open
          frames are kept.
        </p>
      {/if}
    {:else}
      <p class="muted">Counting…</p>
    {/if}
    <div class="buttons">
      {#if result || error}
        <button onclick={close}>Close</button>
      {:else}
        <button onclick={onclose} disabled={busy}>Cancel</button>
        <button class="danger" onclick={confirm} disabled={!preview || busy}>{busy ? "Moving…" : `Move ${preview?.rejects ?? ""} to the trash`}</button>
      {/if}
    </div>
  </div>
</div>

<style>
  .overlay { position: fixed; inset: 0; background: #000a; display: flex; align-items: center; justify-content: center; }
  .dialog { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 16px 22px; max-width: 560px; }
  h2 { margin: 0 0 10px; font-size: 16px; }
  .error { color: var(--reject); }
  .buttons { display: flex; gap: 8px; justify-content: flex-end; margin-top: 14px; }
</style>
