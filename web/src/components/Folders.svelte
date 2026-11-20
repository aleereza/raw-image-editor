<script lang="ts">
  import { onMount } from "svelte";
  import { api, type Browse, type Folder } from "../lib/api";
  import { fileSize, loadFolder, say, ui } from "../lib/state.svelte";

  let folders = $state<Folder[]>([]);
  let browse = $state<Browse | null>(null);
  let browseError = $state("");
  let path = $state("");
  let opening = $state<string | null>(null);

  async function refresh() {
    try {
      folders = await api.folders();
    } catch (error) {
      say((error as Error).message);
    }
  }

  async function show(where?: string) {
    try {
      browse = await api.browse(where);
      browseError = "";
      path = browse.path;
    } catch (error) {
      browseError = (error as Error).message;
    }
  }

  async function open(target: string) {
    if (!target.trim()) {
      say("choose a folder: click one in the list, or type its path");
      return;
    }
    opening = target;
    try {
      const job = await api.open(target.trim());
      say(`opening ${job.path}`);
      let shown = false;
      const timer = setInterval(async () => {
        const mine = ui.jobs.find((j) => j.id === job.id);
        if (!mine) return;
        // The grid can be browsed as soon as the scan has given the folder a first sort; the rest goes on.
        const browsable = ["sorted", "previews", "analysis"].includes(mine.step) || mine.state === "done";
        if (!shown && browsable && mine.folder_id !== null) {
          shown = true;
          opening = null;
          await loadFolder(mine.folder_id);
        }
        if (mine.state === "done" || mine.state === "failed") {
          clearInterval(timer);
          opening = null;
          await refresh();
          if (mine.state === "failed") say(mine.message, 10000);
        }
      }, 300);
    } catch (error) {
      opening = null;
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
    show();
  });

  const job = $derived(ui.jobs.at(-1));
</script>

<section class="folders">
  <h2>Open a shoot folder</h2>
  <div class="browser">
    <div class="where">
      <button onclick={() => browse?.parent && show(browse.parent)} disabled={!browse?.parent} title="Up one folder">↑</button>
      <span class="path" title={browse?.path}>{browse?.path ?? "…"}</span>
    </div>
    {#if browseError}
      <p class="error">{browseError}</p>
    {:else if browse}
      <ul>
        {#each browse.folders as entry (entry.path)}
          <li>
            <button class="open" onclick={() => open(entry.path)} disabled={opening !== null} title="Open this folder for culling">
              {opening === entry.path ? "Opening…" : "Open"}
            </button>
            <button class="name" onclick={() => show(entry.path)} title="Show the folders inside">{entry.name}</button>
            {#if entry.folder_id !== null}<span class="badge">opened before</span>{/if}
          </li>
        {:else}
          <li class="muted">No folders here.</li>
        {/each}
      </ul>
    {/if}
  </div>
  <form onsubmit={(event) => { event.preventDefault(); open(path); }}>
    <input type="text" bind:value={path} placeholder="/path/to/shoot folder" size="70" />
    <button class="primary" disabled={opening !== null}>{opening !== null ? "Opening…" : "Open this path"}</button>
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

  <h2>Opened folders</h2>
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
        <tr><td colspan="6" class="muted">None yet: open one above.</td></tr>
      {/each}
    </tbody>
  </table>
</section>

<style>
  .folders { padding: 16px 20px; overflow: auto; height: 100%; }
  h2 { font-size: 15px; margin: 4px 0 10px; }
  h2:not(:first-child) { margin-top: 22px; }
  .browser { border: 1px solid var(--line); border-radius: 8px; max-width: 900px; margin-bottom: 10px; }
  .where { display: flex; gap: 8px; align-items: center; padding: 6px 8px; border-bottom: 1px solid var(--line); }
  .path { font-family: ui-monospace, monospace; font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  ul { list-style: none; margin: 0; padding: 4px 0; max-height: 46vh; overflow-y: auto; }
  li { display: flex; gap: 8px; align-items: center; padding: 3px 8px; }
  li:hover { background: #ffffff08; }
  .open { padding: 2px 10px; }
  .name { background: none; border: none; padding: 0; text-align: left; color: var(--text); }
  .name:hover { color: var(--accent); }
  form { display: flex; gap: 8px; margin-bottom: 8px; }
  .progress { color: var(--warn); }
  .error { color: var(--reject); padding: 6px 10px; }
  .status { display: flex; flex-direction: column; gap: 4px; margin: 10px 0 0; color: var(--muted); }
  .low { color: var(--warn); }
  table { border-collapse: collapse; width: 100%; max-width: 1000px; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line); }
  th { color: var(--muted); font-weight: 500; }
  .link { background: none; border: none; color: var(--accent); padding: 0; font-weight: 600; }
</style>
