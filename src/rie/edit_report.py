"""The report of `rie edit`: a page with every photo's as-shot and edited previews side by side, the agents' reason
chips, QA, and the run's metrics; plus `edits.json` with every proposal and recipe.

Both go to `<cache>/edits/folder-<id>/` and point at the renders in the render cache, so the page works as long as
the cache holds them. Nothing goes into the photo folder.
"""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any

from .config import Config
from .edit import EditRun, describe_change, metrics

STYLE = """
:root { color-scheme: light dark; --bg: #f4f4f2; --card: #fff; --ink: #1d1d1f; --muted: #6b6b70; --line: #ddd;
  --apply: #2f7d4f; --conservative: #a86a00; --as_shot: #9b3b3b; --abstained: #777; }
@media (prefers-color-scheme: dark) { :root { --bg: #18181a; --card: #232326; --ink: #ececef; --muted: #9a9aa0;
  --line: #36363a; } }
body { margin: 0; padding: 16px; background: var(--bg); color: var(--ink); font: 14px/1.4 system-ui, sans-serif; }
h1 { font-size: 20px; margin: 0 0 4px; } .muted { color: var(--muted); }
table.metrics { border-collapse: collapse; margin: 12px 0 20px; } .metrics td { padding: 2px 12px 2px 0; }
.photo { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 12px; margin: 0 0 16px; }
.photo h2 { font-size: 15px; margin: 0 0 8px; } .review { color: var(--conservative); font-weight: 600; }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.pair figure { margin: 0; } .pair img { width: 100%; height: auto; display: block; border-radius: 4px; }
figcaption { color: var(--muted); font-size: 12px; margin-top: 2px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 0; padding: 0; list-style: none; }
.chip { border: 1px solid var(--line); border-left: 4px solid var(--band); border-radius: 4px; padding: 3px 8px; }
.chip b { margin-right: 4px; } ul.notes { margin: 6px 0 0; padding-left: 18px; color: var(--muted); }
@media (max-width: 700px) { .pair { grid-template-columns: 1fr; } }
"""


def _rel(path: Path | None, base: Path) -> str | None:
    return os.path.relpath(path, base) if path else None


def _photo_html(photo: Any, base: Path) -> str:
    e = html.escape
    before, after = _rel(photo.before, base), _rel(photo.after, base)
    status = '<span class="review">needs a look</span>' if photo.review else '<span class="muted">no review</span>'
    parts = [f'<section class="photo" id="{e(photo.ctx.name)}"><h2>{e(photo.ctx.name)} · {status}</h2>']
    if before or after:
        parts.append('<div class="pair">')
        for label, src in (("as shot", before), ("edited", after)):
            if src:
                parts.append(
                    f'<figure><a href="{e(src)}"><img loading="lazy" src="{e(src)}" alt="{label}"></a>'
                    f"<figcaption>{label}</figcaption></figure>"
                )
        parts.append("</div>")
    chips = describe_change(photo)
    if chips:
        parts.append('<ul class="chips">')
        for chip in chips:
            parts.append(
                f'<li class="chip" style="--band: var(--{e(chip["band"])})" title="{e(chip["why"])}">'
                f"<b>{e(chip['agent'])}</b>{e(chip['value'])} · {e(chip['confidence'])} · {e(chip['why'])}</li>"
            )
        parts.append("</ul>")
    notes = [*photo.reasons, *photo.errors]
    if photo.adjusted:
        notes.append("adjusted once after QA: " + ", ".join(photo.adjusted))
    if notes:
        parts.append('<ul class="notes">' + "".join(f"<li>{e(n)}</li>" for n in notes) + "</ul>")
    parts.append("</section>")
    return "".join(parts)


def _metrics_html(m: dict[str, Any]) -> str:
    rows = [
        ("photos edited", f"{m['photos'] - m['failed']} of {m['photos']}"),
        ("need no review", f"{m['no_review_share']:.0%} ({m['photos'] - m['review']} photos)"),
        ("scenes harmonized", str(m["scenes_harmonized"])),
    ]
    for name in ("geometry", "white_balance", "exposure", "color", "baseline"):
        a = m[name]
        bands = ", ".join(f"{k} {v}" for k, v in sorted(a["bands"].items()))
        rows.append(
            (name.replace("_", " "), f"changed {a['changed']}; {bands}; median confidence {a['median_confidence']}")
        )
    wbm = m["white_balance"]
    rows.append(
        (
            "white balance",
            f"median shift {wbm['median_shift_mired']} mired from the camera; scenes within 100 K: "
            f"{wbm['scenes_within_100k']} of {wbm['scenes']} (widest {wbm['scene_spread_k_max']:.0f} K)",
        )
    )
    ex = m["exposure"]
    rows.append(
        (
            "exposure",
            f"median {ex['ev_median']:+.2f} EV, range {ex['ev_range'][0]:+.2f} to {ex['ev_range'][1]:+.2f}; "
            f"renders with clipped faces {ex['clipped_faces_share']:.0%}",
        )
    )
    q = m["qa"]
    rows.append(("QA", f"adjusted {q['adjusted']} ({q['adjusted_by']}); still failing {q['still_failing']}"))
    t = m["timings"]
    rows.append(
        (
            "time per photo",
            f"{t['per_photo_wall']} s wall; read {t['read']} s, as-shot render {t['render_before']} s, agents "
            f"{t['agents']} s, edited render {t['render_after']} s (medians, several at a time)",
        )
    )
    return (
        '<table class="metrics">'
        + "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(v)}</td></tr>" for k, v in rows)
        + "</table>"
    )


def write_report(run: EditRun, cfg: Config) -> Path:
    """Write `index.html` and `edits.json` for a run; returns the page's path."""
    out = cfg.paths.cache / "edits" / f"folder-{run.folder_id}"
    out.mkdir(parents=True, exist_ok=True)
    m = metrics(run)
    data = {
        "folder_id": run.folder_id,
        "look": run.settings.look.id,
        "metrics": m,
        "photos": [p.to_json() for p in run.photos],
    }
    (out / "edits.json").write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    body = "".join(_photo_html(p, out) for p in run.photos)
    page = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Edits: {html.escape(run.folder.name)}</title><style>{STYLE}</style></head><body>"
        f"<h1>Edits: {html.escape(run.folder.name)}</h1>"
        f"<p class='muted'>look {html.escape(run.settings.look.id)}; previews with the fast profile; "
        "hover a chip for the whole reason</p>"
        f"{_metrics_html(m)}{body}</body></html>"
    )
    page_path = out / "index.html"
    page_path.write_text(page, encoding="utf-8")
    return page_path
