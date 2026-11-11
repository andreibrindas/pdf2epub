"""PDF pages -> visual lines of styled runs, split into body / footnote / page-number regions."""
from dataclasses import dataclass, field

import pymupdf


@dataclass
class Run:
    text: str
    italic: bool
    bold: bool
    sup: bool
    size: float
    font: str
    x0: float = 0.0


@dataclass
class VLine:
    """One visual line: every PDF text line sharing a baseline."""
    page: int        # 1-based PDF page
    y: float         # baseline
    x0: float
    x1: float
    size: float      # dominant (most characters) non-superscript size
    runs: list = field(default_factory=list)
    max_gap: float = 0.0  # widest horizontal gap between fragments (table columns, tabs)
    left: float = 0.0     # text block edges for this page (from the Layout)
    right: float = 0.0

    @property
    def text(self):
        return "".join(r.text for r in self.runs)

    @property
    def indent(self):
        return self.x0 - self.left

    @property
    def rgap(self):
        return self.right - self.x1

    def visible_runs(self):
        return [r for r in self.runs if r.text.strip()]

    def all_bold(self):
        v = [r for r in self.visible_runs() if not r.sup]
        return bool(v) and all(r.bold for r in v)

    def all_italic(self):
        v = [r for r in self.visible_runs() if not r.sup]
        return bool(v) and all(r.italic for r in v)

    def fonts(self):
        return {r.font for r in self.visible_runs()}


def page_lines(page, pno, layout):
    """Group PyMuPDF lines into visual lines by baseline."""
    left = layout.left[pno % 2]
    right = left + layout.text_width
    frags = []
    for b in page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)["blocks"]:
        for l in b.get("lines", []):
            spans = [s for s in l["spans"] if s["text"]]
            vis = [s for s in spans if s["text"].strip()]
            if not vis:
                continue
            main = max(vis, key=lambda s: (s["size"], len(s["text"])))
            frags.append((main["origin"][1], main["size"], l["bbox"], spans))
    frags.sort(key=lambda f: (round(f[0], 0), f[2][0]))
    groups = []
    for f in frags:
        for g in groups:
            if abs(g[0][0] - f[0]) < 2.5 and abs(g[0][1] - f[1]) < 3.5:
                g.append(f)
                break
        else:
            groups.append([f])
    vlines = []
    for g in groups:
        g.sort(key=lambda f: f[2][0])
        base = g[0][0]
        runs = []
        prev_x1 = None
        max_gap = 0.0
        for _, _, bbox, spans in g:
            if prev_x1 is not None:
                gap = bbox[0] - prev_x1
                max_gap = max(max_gap, gap)
                if gap > 1.0 and runs and not runs[-1].text.endswith(" "):
                    runs.append(Run(" ", runs[-1].italic, runs[-1].bold, False, runs[-1].size, runs[-1].font, prev_x1))
            for s in spans:
                fl = s["flags"]
                font = s["font"].split("+")[-1]
                size = s["size"]
                raised = base - s["origin"][1]
                # flag 1 = superscript; also catch small raised spans the PDF did not flag
                sup = bool(fl & 1) or (size < 0.8 * max(x[1] for x in g) and raised > 1.2)
                italic = bool(fl & 2) or "Italic" in font or "Oblique" in font or font.startswith("SitkaI")
                bold = bool(fl & 16) or "Bold" in font
                runs.append(Run(s["text"], italic, bold, sup, size, font, s["bbox"][0]))
            prev_x1 = bbox[2]
        while runs and not runs[0].text.strip():
            runs.pop(0)
        while runs and not runs[-1].text.strip():
            runs.pop()
        if not runs:
            continue
        sizes = {}
        for r in runs:
            if r.sup or not r.text.strip():
                continue
            sizes[round(r.size, 1)] = sizes.get(round(r.size, 1), 0) + len(r.text.strip())
        dom = max(sizes, key=sizes.get) if sizes else max(r.size for r in runs)
        # visible extent (ignore whitespace-only fragments at the ends)
        vis_boxes = [bbox for _, _, bbox, spans in g if any(s["text"].strip() for s in spans)]
        x0 = min(b[0] for b in vis_boxes)
        x1 = max(b[2] for b in vis_boxes)
        vlines.append(VLine(pno, base, x0, x1, dom, runs, max_gap, left, right))
    vlines.sort(key=lambda v: v.y)
    return vlines


def split_regions(vlines, layout):
    """-> (body, footnotes, printed page label or None)"""
    body, foot, label = [], [], None
    lo, hi = layout.folio_size
    for v in vlines:
        t = v.text.strip()
        if v.y < layout.header_max_y:
            continue
        if v.y > layout.folio_min_y and t.isdigit() and lo < v.size < hi:
            label = t
            continue
        if v.size < layout.footnote_max_size:
            foot.append(v)
        else:
            body.append(v)
    return body, foot, label


def load(pdf, layout):
    """-> [{"pno", "body", "foot", "label"}] for every page."""
    pages = []
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc):
            pno = i + 1
            body, foot, label = split_regions(page_lines(page, pno, layout), layout)
            pages.append({"pno": pno, "body": body, "foot": foot, "label": label})
    return pages
