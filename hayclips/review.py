"""review.html at the project root: every selected clip in display order, all rendered styles side by side.

Everything on the page is unreviewed machine output; all text is HTML-escaped.
"""
from __future__ import annotations

import html

from . import jsonio
from .captions.ass import STYLE_NAMES
from .selection import stamp


def write_review(repo, project, results) -> None:
    by_id = {r.clip_id: r for r in results}
    cards = []
    for clip in project.ordered():
        r = by_id.get(clip.id)
        cdir = repo.clip_dir(clip.id)
        rel = f"clips/{clip.id}"
        info = jsonio.read_json(cdir / "render" / "render.json", default=None)
        head = f'<article><h2>#{clip.order} · {html.escape(clip.title)}</h2><p class="meta">clip id {clip.id}</p>'
        if r is not None and r.status != "rendered" or info is None:
            msg = r.message if r is not None else "not rendered"
            cards.append(head + f'<p class="warn">{html.escape(r.status if r else "pending")}: {html.escape(msg)}</p></article>')
            continue
        cut = info["cut"]
        length = cut["end"] - cut["start"]
        players = "".join(
            f'<figure><figcaption>{st} · {STYLE_NAMES[st]}</figcaption>'
            f'<video controls preload="metadata" src="{rel}/{o["path"]}"></video></figure>'
            for st, o in info["outputs"].items())
        checks = "".join(f'<li class="warn">{html.escape(c)}</li>' for c in info.get("checks", []))
        window = repo.load_window(clip.id)
        uncut = "wide.mp4" if window and window.wide else "preview.mp4"
        yt = cdir / "youtube.srt"
        ytxt = " / ".join(b.split("\n", 2)[2].replace("\n", " ")
                          for b in yt.read_text(encoding="utf-8").strip().split("\n\n") if b.count("\n") >= 2) if yt.exists() else ""
        srt = cdir / "render" / "captions.srt"
        lines = ""
        if srt.exists():
            for block in srt.read_text(encoding="utf-8").strip().split("\n\n"):
                parts = block.split("\n", 2)
                if len(parts) == 3:
                    lines += f"<li><code>{html.escape(parts[1][3:8])}</code> {html.escape(parts[2])}</li>"
        hook = info.get("hook")
        cards.append(
            head
            + f'<p class="meta">Source {stamp(cut["source_start"])}–{stamp(cut["source_end"])} · {length:.1f} s · '
            f'cut {html.escape(cut["mode"])} · first word at {info.get("first_word_at") or 0:.2f} s · '
            f'{html.escape(clip.pick_note)}</p>'
            + (f'<p>Hook (draft, needs founder approval): «{html.escape(hook)}»</p>' if hook else "")
            + f'<p class="meta">Framing: {html.escape(info["framing"])} · alignment: '
            f'{html.escape(info["alignment"]["method"])} · audio: {html.escape(info["audio"])}</p>'
            + (f'<ul>{checks}</ul>' if checks else "")
            + f'<p>First 3 s: «{html.escape(info.get("first_3s_text", ""))}»</p><div class="grid">{players}</div>'
            f'<details><summary>Uncut padded window</summary>'
            f'<video controls preload="none" src="{rel}/{uncut}"></video></details>'
            f'<h3>Transcript ({html.escape(info["word_source"])}, unreviewed)</h3><ul>{lines}</ul>'
            + (f'<details><summary>YouTube auto-captions</summary><p>{html.escape(ytxt)}</p></details>' if ytxt else "")
            + f'<p><a href="{rel}/render/captions.srt">SRT</a></p></article>')
    source = html.escape(project.source.get("url", ""))
    (repo.root / "review.html").write_text(
        '<!doctype html><html lang="hy"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        f'<title>HayClips · {html.escape(project.name)}</title><style>body{{font:17px/1.5 system-ui;background:#101827;color:#eef;'
        'max-width:1100px;margin:2rem auto;padding:1rem}article{background:#1d2b40;padding:1.5rem;margin:1rem 0;'
        'border-radius:14px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1rem}'
        'figure{margin:0}figcaption{color:#9ab}video{width:100%;max-height:75vh}a{color:#8bd8ff}'
        'ul{padding-left:1rem;list-style:none}code{color:#9ab;margin-right:.5rem}details{color:#bbc;margin:.5rem 0}'
        '.meta{color:#9ab}.warn{color:#ffcf66}</style>'
        f'<h1>HayClips · {html.escape(project.name)}</h1><p>{source}</p><p>Ձեռքով ստուգիր խոսքը, կտրվածքները և ենթագրերը։ '
        'Ընտրությունը վիրուսային տարածման կանխատեսում չէ։ Everything below is unreviewed machine output. '
        'Which caption style would you post, and how long would fixing it take?</p>' + "".join(cards) + '</html>',
        encoding="utf-8")
