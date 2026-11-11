"""Look inside a PDF before and while writing a recipe (`pdf2epub inspect`).

Every mode prints plain text meant to be read by a person or an AI agent.
Line dumps mark styles inline: _italic_, *bold*, ^{superscript}.
"""
import math
import os
import statistics
from collections import Counter, defaultdict

import pymupdf

from .pdfmodel import load, page_lines, split_regions
from .recipe import Heuristics, Layout

MODES = {
    "layout": "suggested Layout values (margins, text width, header/folio/footnote zones) + font-size histogram",
    "outline": "PDF bookmarks and large-type lines: where chapters start",
    "fonts": "characters per (font, size, flags); flags: 1 sup, 2 italic, 4 serif, 8 mono, 16 bold",
    "styles": "pages where each (font family, size, flags) style occurs, with samples",
    "spans": "raw PyMuPDF spans of PAGES (font, size, flags, baseline, text)",
    "lines": "visual lines of PAGES as the pipeline sees them: region, indent, right gap, baseline, size",
    "headings": "lines that look like headings (large, all bold, or short italic)",
    "gaps": "lines with a wide internal gap (tables, tab stops)",
    "quotes": "lines indented on both sides (block quotes, centred text)",
    "labels": "printed page number found on each page (for page_offset)",
    "hyphens": "line-end hyphens with the next line's first word (for line_end_hyphen)",
    "indents": "distribution of left indents and right edges of body lines",
    "render": "PNG images of PAGES, to look at (default: <book>/work/png)",
}


def parse_pages(spec, n):
    """'7', '7,9', '10-12', 'all' or None -> sorted 1-based page numbers."""
    if not spec or spec == "all":
        return list(range(1, n + 1))
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    bad = [p for p in out if not 1 <= p <= n]
    if bad:
        raise SystemExit(f"pages {bad} outside 1-{n}")
    return sorted(out)


def fmt(v):
    out = []
    for r in v.runs:
        t = r.text
        if r.sup:
            t = "^{" + t + "}"
        if r.italic and t.strip():
            t = "_" + t + "_"
        if r.bold and t.strip():
            t = "*" + t + "*"
        out.append(t)
    return "".join(out)


def _mode_value(values, unit=1.0):
    """Most common value, binned to `unit`, refined to the median inside the winning bin."""
    values = list(values)
    if not values:
        return None
    bins = Counter(round(x / unit) for x in values)
    best = bins.most_common(1)[0][0]
    return round(statistics.median(x for x in values if round(x / unit) == best), 1)


# ----------------------------------------------------------------- layout guess

def suggest_layout(pdf):
    """Guess Layout + a few Heuristics from the PDF itself. -> dict (also used by `new`)."""
    with pymupdf.open(pdf) as doc:
        n = len(doc)
        w, hgt = doc[0].rect.width, doc[0].rect.height
        flat = Layout(page_width=w, left={0: 0.0, 1: 0.0}, text_width=w)
        pages = [page_lines(page, i + 1, flat) for i, page in enumerate(doc)]
    sizes = Counter()
    for vl in pages:
        for v in vl:
            sizes[round(v.size, 1)] += len(v.text.strip())
    if not sizes:
        raise SystemExit("no text layer: this PDF is scanned images and needs OCR first")
    body = sizes.most_common(1)[0][0]
    body_lines = [v for vl in pages for v in vl if abs(v.size - body) < 0.3]

    left = {}
    for parity in (0, 1):
        left[parity] = _mode_value((v.x0 for v in body_lines if v.page % 2 == parity), 1.0)
    if left[0] is None or left[1] is None:
        left[0] = left[1] = left[0] if left[0] is not None else left[1]
    text_width = _mode_value((v.x1 - left[v.page % 2] for v in body_lines), 1.0)

    # line pitch: baseline distance of consecutive body lines / size
    ratios = []
    for vl in pages:
        bl = [v for v in vl if abs(v.size - body) < 0.3]
        for a, b in zip(bl, bl[1:]):
            r = (b.y - a.y) / body
            if 1.0 < r < 1.6:
                ratios.append(r)
    pitch = Counter(round(r, 3) for r in ratios).most_common(1)[0][0] if ratios else 1.25

    # folio: digit-only short lines in the bottom 15% of the page
    folios = [v for vl in pages for v in vl
              if v.text.strip().isdigit() and len(v.text.strip()) <= 4 and v.y > 0.85 * hgt]
    folio_min_y, folio_size = float("inf"), (0.0, 99.0)
    if len({v.page for v in folios}) > 0.3 * n:
        folio_min_y = math.floor(min(v.y for v in folios)) - 2
        folio_size = (round(min(v.size for v in folios) - 0.5, 1), round(max(v.size for v in folios) + 0.5, 1))

    # running heads: a line in the top 12% set apart from the text below it
    heads = []
    for vl in pages:
        if len(vl) >= 2 and vl[0].y < 0.12 * hgt and vl[1].y - vl[0].y > 1.8 * pitch * body:
            heads.append(vl[0].y)
    header_max_y = math.ceil(max(heads)) + 2 if len(heads) > 0.2 * n else 0.0

    # footnotes: a smaller size used mostly low on the page
    footnote_max_size, fn_size = 0.0, None
    low = defaultdict(list)
    for vl in pages:
        for v in vl:
            s = round(v.size, 1)
            if 0.55 * body < s < body - 0.5 and v.y < folio_min_y:
                low[s].append(v.y / hgt)
    cands = [(sizes[s], s) for s, ys in low.items()
             if sizes[s] > 0.01 * sizes[body] and statistics.median(ys) > 0.55]
    if cands:
        fn_size = max(cands)[1]
        # just above the footnote size: captions or small body text in between stay body
        footnote_max_size = round(fn_size + 0.3 * (body - fn_size), 1)

    big = sorted((s for s in sizes if s >= body * 1.12), reverse=True)
    chapter_min_size = round(min(big) - 0.3, 1) if big else round(body * 1.3, 1)
    return {
        "n_pages": n, "page_size": (round(w, 1), round(hgt, 1)), "body_size": body,
        "footnote_size": fn_size, "sizes": sizes,
        "layout": Layout(page_width=round(w, 1), left=left, text_width=text_width,
                         header_max_y=header_max_y, folio_min_y=folio_min_y,
                         folio_size=folio_size, footnote_max_size=footnote_max_size),
        "line_pitch": pitch, "chapter_min_size": chapter_min_size,
    }


def suggest_sections(pdf, layout, chapter_min_size):
    """Section starts from PDF bookmarks, else from large-type lines.

    -> ([(title, first, last)], "bookmarks" | "large type" | "nothing")
    """
    with pymupdf.open(pdf) as doc:
        n = len(doc)
        toc = [(lvl, t.strip(), p) for lvl, t, p in doc.get_toc() if p >= 1]
    starts = []
    if toc:
        top = min(lvl for lvl, _, _ in toc)
        starts = [(t, p) for lvl, t, p in toc if lvl == top]
    else:
        for pg in load(pdf, layout):
            big = [v for v in pg["body"] if v.size >= chapter_min_size]
            if big:
                starts.append((" ".join(v.text.strip() for v in big)[:80], pg["pno"]))
    if not starts:
        return [("TODO title", 1, n)], "nothing"
    out = []
    for i, (t, p) in enumerate(starts):
        last = starts[i + 1][1] - 1 if i + 1 < len(starts) else n
        if last >= p:
            out.append((t, p, last))
    return out, ("bookmarks" if toc else "large type")


# ----------------------------------------------------------------- modes

def run(mode, pdf, layout=None, heur=None, pages_spec=None, out_dir=None):
    heur = heur or Heuristics()
    if mode not in MODES:
        raise SystemExit(f"unknown mode {mode!r}; modes:\n" + "\n".join(f"  {k:9s} {v}" for k, v in MODES.items()))

    if mode == "layout":
        s = suggest_layout(pdf)
        L = s["layout"]
        print(f"pages: {s['n_pages']}   page size: {s['page_size'][0]} x {s['page_size'][1]} pt")
        print(f"body text size: {s['body_size']}   footnote size: {s['footnote_size']}")
        print("\nsuggested:")
        print(f"  Layout(page_width={L.page_width}, left={L.left}, text_width={L.text_width},")
        print(f"         header_max_y={L.header_max_y}, folio_min_y={L.folio_min_y}, folio_size={L.folio_size},")
        print(f"         footnote_max_size={L.footnote_max_size})")
        print(f"  Heuristics(line_pitch={s['line_pitch']}, chapter_min_size={s['chapter_min_size']})")
        print("\nfont sizes (characters):")
        for size, c in sorted(s["sizes"].items(), key=lambda kv: -kv[0]):
            print(f"  {size:5.1f} {c:8d}")
        print("\ncheck with: inspect <book> lines <a few pages>; regions are B(ody), F(ootnote), H(eader), N(page number)")
        return

    if mode == "outline":
        with pymupdf.open(pdf) as doc:
            toc = doc.get_toc()
        print("PDF bookmarks:" if toc else "PDF bookmarks: none")
        for lvl, t, p in toc:
            print(f"  {'  ' * (lvl - 1)}p{p:<4d} {t}")
        print(f"\nlines at size >= {heur.chapter_min_size} (chapter_min_size):")
        for pg in load(pdf, layout):
            for v in pg["body"]:
                if v.size >= heur.chapter_min_size:
                    print(f"  p{pg['pno']:<4d} sz={v.size:4.1f} | {fmt(v)[:90]}")
        return

    if mode in ("fonts", "styles", "spans", "render"):
        with pymupdf.open(pdf) as doc:
            sel = parse_pages(pages_spec, len(doc))
            if mode in ("spans", "render") and not pages_spec:
                raise SystemExit(f"{mode}: give pages, e.g. 12 or 12,15-17")
            if mode == "render":
                os.makedirs(out_dir, exist_ok=True)
                for p in sel:
                    path = os.path.join(out_dir, f"p{p:03d}.png")
                    doc[p - 1].get_pixmap(dpi=110).save(path)
                    print(path)
                return
            counts = Counter()
            where = defaultdict(list)
            for p in sel:
                page = doc[p - 1]
                if mode == "spans":
                    print(f"===== page {p}  {page.rect.width:.1f} x {page.rect.height:.1f}")
                for b in page.get_text("dict")["blocks"]:
                    for l in b.get("lines", []):
                        if mode == "spans":
                            x0, y0, x1, _ = l["bbox"]
                            parts = [f"[{s['font'].split('+')[-1]}|{s['size']:.1f}|f{s['flags']}|y{s['origin'][1]:.1f}]{s['text']!r}"
                                     for s in l["spans"]]
                            print(f"x={x0:6.1f}-{x1:6.1f} y={y0:6.1f}  " + " ".join(parts))
                            continue
                        for s in l["spans"]:
                            t = s["text"].strip()
                            if not t:
                                continue
                            font = s["font"].split("+")[-1]
                            if mode == "fonts":
                                counts[(font, round(s["size"], 1), s["flags"])] += len(t)
                            else:
                                where[(font.split("-")[0], round(s["size"], 1), s["flags"])].append(
                                    (p, round(l["bbox"][0], 1), t[:60]))
        if mode == "fonts":
            for (font, size, flags), c in counts.most_common():
                print(f"{c:8d}  {font:32s} {size:5.1f}  f{flags}")
        elif mode == "styles":
            for k, v in sorted(where.items(), key=lambda kv: -len(kv[1])):
                pgs = sorted({p for p, _, _ in v})
                print(f"{k}  {len(v)} spans on {len(pgs)} pages: {pgs[:30]}{' ...' if len(pgs) > 30 else ''}")
                for item in v[:3]:
                    print("     ", item)
        return

    # modes over the pipeline's visual lines
    if layout is None:
        layout = suggest_layout(pdf)["layout"]
        print(f"(no recipe: using the suggested layout {layout})\n")
    with pymupdf.open(pdf) as doc:
        sel = parse_pages(pages_spec, len(doc))
        if mode == "lines" and not pages_spec:
            raise SystemExit("lines: give pages, e.g. 12 or 12,15-17")
        data = []
        for p in sel:
            vl = page_lines(doc[p - 1], p, layout)
            body, foot, label = split_regions(vl, layout)
            data.append((p, vl, body, foot, label))

    if mode == "lines":
        lo, hi = layout.folio_size
        for p, vl, body, foot, label in data:
            print(f"===== page {p}  printed label={label}")
            ids_b, ids_f = {id(v) for v in body}, {id(v) for v in foot}
            for v in vl:
                reg = "B" if id(v) in ids_b else "F" if id(v) in ids_f else (
                    "H" if v.y < layout.header_max_y else "N")
                print(f"{reg} ind={v.indent:6.1f} rgap={v.rgap:6.1f} y={v.y:6.1f} sz={v.size:4.1f} "
                      f"gap={v.max_gap:5.1f} | {fmt(v)}")
    elif mode == "headings":
        for p, _, body, _, _ in data:
            for v in body:
                if v.size >= heur.chapter_min_size or v.all_bold() or (v.all_italic() and v.rgap > 40):
                    print(f"p{p:<4d} ind={v.indent:6.1f} rgap={v.rgap:6.1f} sz={v.size:4.1f} | {fmt(v)[:100]}")
    elif mode == "gaps":
        for p, _, body, _, _ in data:
            for v in body:
                if v.max_gap > 25:
                    print(f"p{p:<4d} ind={v.indent:6.1f} gap={v.max_gap:5.1f} | {fmt(v)[:100]}")
    elif mode == "quotes":
        for p, _, body, _, _ in data:
            for v in body:
                if v.indent > 12 and v.rgap > 12 and v.size < heur.chapter_min_size and not v.all_bold():
                    print(f"p{p:<4d} ind={v.indent:6.1f} rgap={v.rgap:6.1f} sz={v.size:4.1f} | {fmt(v)[:100]}")
    elif mode == "labels":
        offs = Counter(int(label) - p for p, *_, label in data if label and label.isdigit())
        for p, *_, label in data:
            print(f"p{p:<4d} -> {label}")
        if offs:
            print(f"\nprinted - PDF page: {dict(offs.most_common())}  (page_offset = the dominant value)")
    elif mode == "hyphens":
        n = 0
        for p, _, body, _, _ in data:
            for a, b in zip(body, body[1:]):
                t = a.text.rstrip()
                if t.endswith("-") and not t.endswith(" -") and b.text.split():
                    n += 1
                    word = t.split()[-1] if t.split() else t
                    print(f"p{p:<4d} {word:>24s} | {b.text.split()[0]}")
        print(f"\n{n} line-end hyphens. If the halves are mostly syllables of one word, set "
              f"Heuristics(line_end_hyphen='drop'); if they are real compounds (într-adevăr), keep.")
    elif mode == "indents":
        ind, right = Counter(), Counter()
        sample = {}
        for p, _, body, _, _ in data:
            for v in body:
                d = round(v.indent)
                ind[d] += 1
                sample.setdefault(d, (p, v.text.strip()[:60]))
                right[round(v.rgap)] += 1
        print("left indent: lines  (sample)")
        for k, c in sorted(ind.items()):
            print(f"  {k:5d}: {c:6d}  {sample[k]}")
        print("right gap, most common:", right.most_common(8))
