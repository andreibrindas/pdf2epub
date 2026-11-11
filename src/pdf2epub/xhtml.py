"""Blocks -> XHTML documents."""
import re

from .blocks import note_refs
from .inline import NOTE, PB, attr, esc, render


def xhtml_doc(ctx, title, body, epub_type=None, body_class=None, head_extra=""):
    lang = attr(ctx.recipe.language)
    bt = f' epub:type="{epub_type}"' if epub_type else ""
    bc = f' class="{body_class}"' if body_class else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}" lang="{lang}">
<head>
<meta charset="UTF-8"/>
<title>{esc(title)}</title>
<link rel="stylesheet" type="text/css" href="style.css"/>{head_extra}
</head>
<body{bc}>
<section{bt}>
{body}
</section>
</body>
</html>
"""


def chapter_parts(ctx, sec, lines):
    """-> (label or None, title) for a section's h1, via the recipe's chapter_heading hook."""
    hook = ctx.recipe.chapter_heading
    if sec.kind == "chapter" and hook:
        return hook(sec, lines)
    return None, " ".join(v.text.strip() for v in lines)


def h1_html(ctx, sec, b, n=1):
    """The section's n-th large-type title; the first one gets the id "<section>-title"."""
    pb = "".join(render(ctx, [c]) for c in b["chunks"] if c[0] == PB)
    label, name = chapter_parts(ctx, sec, b["lines"])
    hid = f"{sec.id}-title" if n == 1 else f"{sec.id}-title-{n}"
    if sec.kind == "chapter":
        num = f'<span class="chap-num">{esc(label)}</span>' if label else ""
        return (f'<h1 class="chapter" id="{hid}">{pb}{num}'
                f'<span class="chap-title">{esc(name)}</span></h1>')
    return f'<h1 id="{hid}">{pb}{esc(name)}</h1>'


HEADING_CLASS = {0: "h-caps", 1: "h-bold", 2: "h-bolditalic", 3: "h-italic"}


def heading_html(ctx, b, hid):
    inner = render(ctx, b["chunks"])
    # a heading set entirely in its own style: drop the now redundant <b>/<i>
    plain = re.sub(r"</?[bi]>", "", inner)
    rank = b["rank"]
    if rank in (0, 1) and "<i>" not in inner:
        inner = plain
    elif rank == 2 and inner.count("<b>") and "<i>" in inner and not re.search(
            r"(^|</b>)[^<]+", inner.replace("<b><i>", "").replace("</i></b>", "")):
        inner = plain
    elif rank == 3 and re.fullmatch(r"(<span[^>]*></span>)*<i>[^<]*</i>", inner):
        inner = plain
    lvl = b["level"]
    return f'<h{lvl} id="{hid}" class="{HEADING_CLASS[rank]}">{inner}</h{lvl}>'


def table_html(ctx, b):
    rows = b["rows"]
    head, body = rows[0], rows[1:]
    out = ['<table class="compare">', "<thead><tr>",
           f'<th scope="col">{re.sub(r"</?b>", "", render(ctx, head[1]))}</th>',
           f'<th scope="col">{re.sub(r"</?b>", "", render(ctx, head[2]))}</th>',
           "</tr></thead>", "<tbody>"]
    for _, left, right in body:
        out.append(f"<tr><td>{render(ctx, left)}</td><td>{render(ctx, right)}</td></tr>")
    out.append("</tbody></table>")
    return "\n".join(out)


def section_html(ctx, sec, blocks, notes):
    """-> (body XHTML, [(level, id, text)] headings, h1 label or None)"""
    ctx.cur_section = sec.id
    out = []
    used_notes = []
    hcount = 0
    h1count = 0
    toc_entries = []
    h1_label = None
    for b in blocks:
        t = b["type"]
        used_notes.extend(note_refs([b]))
        if t == "h1":
            h1count += 1
            out.append(h1_html(ctx, sec, b, h1count))
            if h1_label is None:
                h1_label = chapter_parts(ctx, sec, b["lines"])[0]
        elif t == "subtitle":
            out.append(f'<p class="subtitle">{render(ctx, b["chunks"])}</p>')
        elif t == "h":
            hcount += 1
            hid = f"{sec.id}-h{hcount}"
            out.append(heading_html(ctx, b, hid))
            text = re.sub(r"<[^>]+>", "", render(ctx, [c for c in b["chunks"] if c[0] != NOTE])).strip()
            toc_entries.append((b["level"], hid, text))
        elif t == "p":
            out.append(f"<p>{render(ctx, b['chunks'], link=True)}</p>")
        elif t == "epigraph":
            out.append(f'<blockquote class="epigraph"><p>{render(ctx, b["chunks"])}</p></blockquote>')
        elif t == "biblio":
            out.append(f'<p class="bib">{render(ctx, b["chunks"], link=True)}</p>')
        elif t == "pbonly":
            out.append(f"<div>{render(ctx, b['chunks'])}</div>")
        elif t == "table":
            pb = "".join(render(ctx, [c]) for c in b["chunks"] if c[0] == PB)
            if pb:
                out.append(f"<div>{pb}</div>")
            out.append(table_html(ctx, b))
    # footnotes first referenced in this section, at its end
    home = [n for n in dict.fromkeys(used_notes) if ctx.note_home.get(n) == sec.id and n in notes]
    if home:
        out.append(f'<section class="footnotes" epub:type="footnotes" role="doc-endnotes" '
                   f'aria-label="{attr(ctx.recipe.label("notes"))}">')
        out.append('<hr class="fn-sep"/>')
        for n in home:
            body = render(ctx, notes[n]["chunks"], notes_ok=False, link=True)
            out.append(f'<aside epub:type="footnote" role="doc-footnote" id="fn{n}" class="fn">'
                       f'<p><a class="fn-back" href="#fnref{n}">{n}</a> {body}</p></aside>')
        out.append("</section>")
    return "\n".join(out), toc_entries, h1_label
