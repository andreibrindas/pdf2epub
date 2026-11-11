"""Visual lines -> blocks (headings, paragraphs, epigraphs, tables, footnotes).

A block is a dict: {"type": "h1" | "subtitle" | "h" | "p" | "epigraph" | "table"
| "biblio" | "pbonly", "chunks": [...], ...}. Headings ("h") carry a style
"rank" (0 = all-caps bold, 1 = bold, 2 = bold-italic or bold "A." list item,
3 = italic) and, after nest_headings, an XHTML "level".
"""
import re

from .inline import (NOTE, chunks_text, join_lines, line_chunks, lstrip_chunks,
                     pb_chunks)


# ----------------------------------------------------------------- footnotes

def parse_notes(ctx, pages):
    """Footnotes numbered through the whole book, each starting with a superscript number.

    -> ({n: {"chunks", "page"}}, [n in reading order])
    """
    notes = {}
    order = []
    cur = None
    drop = ctx.h.line_end_hyphen == "drop"
    for pg in pages:
        for v in pg["foot"]:
            runs = ctx.clean_runs(v)
            vis = [r for r in runs if r.text.strip()]
            if not vis:
                continue
            first = vis[0]
            m = re.fullmatch(r"\s*(\d+)\s*", first.text) if first.sup else None
            if m:
                n = int(m.group(1))
                rest = runs[runs.index(first) + 1:]
                if n in notes:
                    ctx.warn(f"duplicate note {n} on p{pg['pno']}")
                cur = n
                order.append(n)
                notes[n] = {"chunks": lstrip_chunks(line_chunks(ctx, v, rest)), "page": pg["pno"]}
            else:
                if cur is None:
                    ctx.warn(f"note continuation without a note on p{pg['pno']}: {v.text[:60]!r}")
                    continue
                notes[cur]["chunks"] = join_lines(notes[cur]["chunks"], line_chunks(ctx, v, runs), drop)
    return notes, order


# ----------------------------------------------------------------- line styles

def _visible(ctx, v):
    return [r for r in ctx.clean_runs(v) if r.text.strip() and not r.sup]


def italic_ratio(ctx, v):
    vis = _visible(ctx, v)
    tot = sum(len(r.text.strip()) for r in vis)
    return sum(len(r.text.strip()) for r in vis if r.italic) / tot if tot else 0.0


def bold_ratio(ctx, v):
    vis = _visible(ctx, v)
    tot = sum(len(r.text.strip()) for r in vis)
    return sum(len(r.text.strip()) for r in vis if r.bold) / tot if tot else 0.0


def heading_rank(ctx, v):
    """0 = upper-case bold, 1 = bold, 2 = bold-italic / bold 'A.' list, 3 = italic, None = not a heading style."""
    h = ctx.h
    text = v.text.strip()
    letters = [ch for ch in text if ch.isalpha()]
    upper = letters and sum(ch.isupper() for ch in letters) / len(letters) > h.caps_ratio
    br = bold_ratio(ctx, v)
    ir = italic_ratio(ctx, v)
    if br > h.bold_ratio:
        if upper and len(letters) > 3:
            return 0
        if ir > h.italic_ratio or re.match(r"^[A-D]\.\s", text):
            return 2
        return 1
    if ir >= h.italic_ratio:
        return 3
    return None


def pitch(ctx, size):
    return size * ctx.h.line_pitch


# ----------------------------------------------------------------- body blocks

def build_blocks(ctx, sec, pages):
    """Blocks of one section, with page-break markers for every page in its range."""
    h = ctx.h
    kind = sec.kind
    drop = h.line_end_hyphen == "drop"
    blocks = []
    cur = None  # paragraph block still open for continuation lines
    prev_line = None
    pending_pb = []

    # flatten the section's pages; remember blank pages so their markers land in order
    lines = []  # (pno, first line on its page, VLine)
    blank_before = {}  # line index -> blank page numbers that precede it
    blanks = []
    for pg in pages[sec.first - 1:sec.last]:
        body = [v for v in pg["body"] if [r for r in ctx.clean_runs(v) if r.text.strip()]]
        if not body:
            blanks.append(pg["pno"])
            continue
        if blanks:
            blank_before[len(lines)] = blanks
            blanks = []
        for i, v in enumerate(body):
            lines.append((pg["pno"], i == 0, v))
    trailing_blanks = blanks

    def start_block(b):
        nonlocal cur
        if pending_pb:
            b["chunks"] = pb_chunks(pending_pb) + b["chunks"]
            pending_pb.clear()
        blocks.append(b)
        cur = b if b["type"] in ("p", "epigraph", "biblio") else None
        return b

    i = 0
    while i < len(lines):
        pno, page_first, v = lines[i]
        pending_pb.extend(blank_before.get(i, []))
        if page_first:
            pending_pb.append(pno)
        text = v.text.strip()

        # chapter title (large type); consecutive large lines on the same page form one title
        if v.size >= h.chapter_min_size:
            title_lines = [v]
            while (i + 1 < len(lines) and lines[i + 1][2].size >= h.chapter_min_size
                   and lines[i + 1][0] == pno):
                i += 1
                title_lines.append(lines[i][2])
            start_block({"type": "h1", "lines": title_lines, "chunks": []})
            prev_line = title_lines[-1]
            i += 1
            continue

        # centred subtitle right after the chapter title
        m = h.subtitle_margin
        if blocks and blocks[-1]["type"] == "h1" and v.indent > m and v.rgap > m and abs(v.indent - v.rgap) < 6:
            start_block({"type": "subtitle", "chunks": line_chunks(ctx, v)})
            prev_line = v
            i += 1
            continue

        # two-column table on a page listed in recipe.tables
        split_x = ctx.recipe.tables.get(pno)
        if split_x is not None and v.max_gap > 10 and v.indent > 10 and v.rgap > 10:
            rows = []
            while i < len(lines) and lines[i][0] == pno and lines[i][2].max_gap > 10:
                tv = lines[i][2]
                runs = ctx.clean_runs(tv)
                left = [r for r in runs if r.x0 < split_x]
                right = [r for r in runs if r.x0 >= split_x]
                rows.append((tv.all_bold(), line_chunks(ctx, tv, left), line_chunks(ctx, tv, right)))
                i += 1
            start_block({"type": "table", "rows": rows, "chunks": []})
            prev_line = lines[i - 1][2]
            continue

        # paragraph boundary?
        new_para = True
        if cur is not None and prev_line is not None:
            new_para = False
            same_page = prev_line.page == v.page
            if kind == "biblio":
                if same_page and v.y - prev_line.y > h.biblio_gap * pitch(ctx, prev_line.size):
                    new_para = True
                elif not same_page and prev_line.rgap > h.short_line_rgap:
                    new_para = True  # the entry ended with a short line at the foot of the previous page
            else:
                if v.indent > h.para_indent:
                    new_para = True
                elif same_page and v.y - prev_line.y > h.para_gap * pitch(ctx, prev_line.size):
                    new_para = True
                elif prev_line.rgap > h.short_line_rgap:
                    new_para = True

        # headings: a short indented line in a heading style that stands alone
        if new_para and kind != "biblio":
            rank = heading_rank(ctx, v)
            nxt = lines[i + 1][2] if i + 1 < len(lines) else None
            standalone = (
                nxt is None
                or nxt.indent > h.para_indent
                or nxt.size >= h.chapter_min_size
                or (nxt.page == v.page and nxt.y - v.y > h.para_gap * pitch(ctx, v.size))
            )
            if rank is not None and standalone and len(text) < h.heading_max_chars and v.indent > h.para_indent:
                start_block({"type": "h", "rank": rank, "chunks": line_chunks(ctx, v), "line": v})
                prev_line = v
                i += 1
                continue
        elif new_para and kind == "biblio":
            if bold_ratio(ctx, v) > h.bold_ratio:
                start_block({"type": "h", "rank": 0, "chunks": line_chunks(ctx, v), "line": v})
                prev_line = v
                i += 1
                continue

        if new_para:
            btype = "biblio" if kind == "biblio" else "p"
            start_block({"type": btype, "chunks": line_chunks(ctx, v), "lines": [v]})
        else:
            if pending_pb:
                # page turn inside a paragraph: the marker goes between the two lines
                cur["chunks"] = join_lines(cur["chunks"], pb_chunks(pending_pb), drop)
                pending_pb.clear()
                cur["chunks"] = cur["chunks"] + lstrip_chunks(line_chunks(ctx, v))
            else:
                cur["chunks"] = join_lines(cur["chunks"], line_chunks(ctx, v), drop)
            cur["lines"].append(v)
        prev_line = v
        i += 1

    pending_pb.extend(trailing_blanks)
    if pending_pb:
        # blank pages at the end of the section: hang their markers on the last text block
        for b in reversed(blocks):
            if b["type"] in ("p", "epigraph", "biblio"):
                b["chunks"] = b["chunks"] + pb_chunks(pending_pb)
                break
        else:
            blocks.append({"type": "pbonly", "chunks": pb_chunks(pending_pb)})
        pending_pb.clear()

    # epigraph: an italic-dominant first paragraph of a chapter
    if kind == "chapter":
        for b in blocks:
            if b["type"] in ("h1", "subtitle"):
                continue
            if b["type"] == "h" and not any(x["type"] == "p" for x in blocks[:blocks.index(b)]):
                continue
            if b["type"] == "p":
                runs = [r for v in b["lines"] for r in _visible(ctx, v)]
                tot = sum(len(r.text.strip()) for r in runs)
                it = sum(len(r.text.strip()) for r in runs if r.italic)
                if tot and it / tot > h.epigraph_italic:
                    b["type"] = "epigraph"
            break
    return blocks


def nest_headings(blocks):
    """Nest headings by style rank so levels never skip (h2 > h3 > h4 ...)."""
    stack = []
    for b in blocks:
        if b["type"] != "h":
            continue
        while stack and stack[-1] >= b["rank"]:
            stack.pop()
        stack.append(b["rank"])
        b["level"] = min(1 + len(stack), 6)


def note_refs(blocks):
    """Note numbers referenced by these blocks, in reading order."""
    out = []
    for b in blocks:
        out.extend(c[1] for c in b["chunks"] if c[0] == NOTE)
        for row in b.get("rows", []):
            for side in row[1:]:
                out.extend(c[1] for c in side if c[0] == NOTE)
    return out


def describe(blocks):
    """One line per structural block, for the build report."""
    out = []
    for b in blocks:
        if b["type"] == "h1":
            out.append(("h1", " / ".join(v.text.strip() for v in b["lines"])))
        elif b["type"] == "table":
            out.append(("table", f"{len(b['rows'])} rows"))
        elif b["type"] == "h":
            out.append((f"h{b['level']} r{b['rank']}", chunks_text(b["chunks"]).strip()))
        elif b["type"] in ("subtitle", "epigraph"):
            out.append((b["type"], chunks_text(b["chunks"]).strip()))
    n = sum(1 for b in blocks if b["type"] in ("p", "biblio"))
    return out, n
