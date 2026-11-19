<script lang="ts">
  import { onMount } from "svelte";
  import { api, type Folder } from "../lib/api";
  import { fileSize, loadFolder, say, ui } from "../lib/state.svelte";

  let folders = $state<Folder[]>([]);
  let path = $state("");
  let busy = $state(false);

  async function refresh() {
    try {
      folders = await api.folders();
    } catch (error) {
      say(`could not list folders: ${(error as Error).message}`);
    }
  }

  async function open() {
    if (!path.trim()) return;
    busy = true;
    try {
      const job = await api.open(path.trim());
      say(`opening ${job.path}`);
      let shown = false;
      const timer = setInterval(async () => {
        const mine = ui.jobs.find((j) => j.id === job.id);
        if (!mine) return;
        // The grid can be browsed as soon as the scan has given the folder a first sort; the rest goes on.
        const browsable = ["sorted", "previews", "analysis"].includes(mine.step) || mine.state === "done";
        if (!shown && browsable && mine.folder_id !== null) {
          shown = true;
          busy = false;
          await loadFolder(mine.folder_id);
        }
        if (mine.state === "done" || mine.state === "failed") {
          clearInterval(timer);
          busy = false;
          await refresh();
          if (mine.state === "failed") say(mine.message, 10000);
        }
      }, 300);
    } catch (error) {
      busy = false;
      say((error as Error).message, 8000);
    }
  }

  async function restore(folder: Folder) {
    if (!confirm(`Move ${folder.in_trash} trashed files of ${folder.name} back from the trash?`)) return;
    try {
      const result = await api.restoreFolder(folder.id);
      say(`restored ${result.restored} files`);
      await refresh();
    } catch (error) {
      say((error as Error).message, 8000);
    }
  }

  onMount(() => {
    refresh();
    if (ui.status?.photos_root && !path) path = `${ui.status.photos_root}/`;
  });

  const job = $derived(ui.jobs.at(-1));
</script>

<section class="folders">
  <form onsubmit={(event) => { event.preventDefault(); open(); }}>
    <input type="text" bind:value={path} placeholder="/path/to/shoot folder" size="70" />
    <button class="primary" disabled={busy}>{busy ? "Opening…" : "Open folder"}</button>
  </form>
  {#if job && (job.state === "running" || job.state === "queued")}
    <p class="progress">{job.path}: {job.step || "queued"} {job.total ? `${job.done} of ${job.total}` : ""}</p>
  {/if}

  {#if ui.status}
    <div class="status">
      <span>Photo drive: <b>{ui.status.drive.configured ? ui.status.drive.state : "not configured"}</b></span>
      {#if ui.status.space}
        <span class:low={ui.status.space.low}>
          Trash on the drive: {fileSize(ui.status.space.trash_bytes)} in {ui.status.space.trash_files} files ·
          {fileSize(ui.status.space.free_bytes)} free
          {#if ui.status.space.low}· low on space: empty the trash when you are ready{/if}
        </span>
      {/if}
      {#if !ui.status.face_models}<span class="low">Face models missing: run scripts/fetch_models.py</span>{/if}
      {#each ui.status.trash_autoempty as finding}<span class="low">Something may empty the trash: {finding}</span>{/each}
    </div>
  {/if}

  <table>
    <thead>
      <tr><th>Folder</th><th>Photos</th><th>Analysed</th><th>Culled</th><th>In the trash</th><th></th></tr>
    </thead>
    <tbody>
      {#each folders as folder (folder.id)}
        <tr>
          <td>
            <button class="link" onclick={() => loadFolder(folder.id)} title={folder.path}>{folder.name}</button>
            {#if !folder.available}<span class="badge warn">drive away</span>{/if}
          </td>
          <td>{folder.photos}</td>
          <td>{folder.analyzed_at ? "yes" : "…"}</td>
          <td>{folder.culled_at ? folder.culled_at.slice(0, 10) : ""}</td>
          <td>{folder.in_trash || ""}</td>
          <td>{#if folder.in_trash}<button onclick={() => restore(folder)}>Restore rejects</button>{/if}</td>
        </tr>
      {:else}
        <tr><td colspan="6" class="muted">No folders yet: open one above, or run <code>rie open FOLDER</code>.</td></tr>
      {/each}
    </tbody>
  </table>
</section>

<style>
  .folders { padding: 20px; overflow: auto; height: 100%; }
  form { display: flex; gap: 8px; margin-bottom: 12px; }
  .progress { color: var(--warn); }
  .status { display: flex; flex-direction: column; gap: 4px; margin: 10px 0 18px; color: var(--muted); }
  .low { color: var(--warn); }
  table { border-collapse: collapse; width: 100%; max-width: 1000px; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line); }
  th { color: var(--muted); font-weight: 500; }
  .link { background: none; border: none; color: var(--accent); padding: 0; font-weight: 600; }
</style>
