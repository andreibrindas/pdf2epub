"""Scanned books: recipe, page naming, contact sheets, page renders + OCR, figure crops, briefs.

A scanned PDF has no usable text layer, so the text comes from transcription: each part
(chapter) is transcribed by an agent from the page images, with Tesseract OCR as a draft,
into pandoc Markdown under ``books/<slug>/md/``. ``scanbuild.py`` checks that Markdown and
turns it into the EPUB. The workflow is in docs/scanned-books.md.

Page names. Every PDF page has a name used for its files and its marker in the Markdown:
a page with a printed folio is ``p-007`` / ``<!-- p.7 -->`` (roman: ``p-iv`` /
``<!-- p.iv -->``); a page without one is ``x-003`` / ``<!-- x.3 -->`` (3 = PDF page).
Folios come from ``ScanRecipe.folios``; with ``folios=None`` every page is numbered by its
PDF page number.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import string
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .recipe import LABELS

PKG = Path(__file__).resolve().parent
PART_FILE = re.compile(r"\d{2,}-[A-Za-z0-9_-]+\.md")
FIG_KEY = re.compile(r"[A-Za-z0-9_-]+")
OCR_LANGS = {"en": "eng", "ro": "ron"}  # BCP 47 primary tag -> Tesseract language

# ----------------------------------------------------------------- recipe


@dataclass
class Part:
    """One transcription unit: a Markdown file under md/ covering a PDF page range."""
    file: str                    # file name under md/, "NN-name.md"; files are joined in recipe order
    title: str                   # chapter title as in the book's contents page (the transcriber's h1)
    first: int                   # first PDF page, 1-based
    last: int                    # last PDF page, inclusive
    label: Optional[str] = None  # printed label over the title ("Chapter One", "3"): # Title {label="..."}
    notes: str = ""              # extra instructions for this part's transcriber

    @property
    def stem(self):
        return self.file[:-3]


@dataclass
class Fig:
    """A figure cropped from a page render into work/figures/<key>.png."""
    page: int                    # PDF page
    box: tuple                   # (x0, y0, x1, y1) as fractions of the upright page
    erase: tuple = ()            # boxes (same units) painted white first: pen marks, stray captions


@dataclass
class ScanRecipe:
    title: str
    author: str
    parts: list                            # [Part], in reading order
    language: str = "en"                   # BCP 47
    subtitle: Optional[str] = None
    publisher: Optional[str] = None
    date: Optional[str] = None             # publication date, e.g. "1971"
    rights: Optional[str] = None
    id_seed: Optional[str] = None          # stable seed for the uuid5 book id (default: title + author)
    # printed page numbers: [(first PDF page, last PDF page, folio of the first page)], the folio
    # an int (arabic) or a roman string ("iv", case kept). Pages outside every range, or in a range
    # whose folio is None, are unnumbered (x-NNN). None: every page is numbered by its PDF page.
    folios: Optional[list] = None
    omit: list = field(default_factory=list)   # [(first, last, reason)] pages not transcribed
    rotate: dict = field(default_factory=dict)  # PDF page -> clockwise degrees (90/180/270) to set it upright
    ocr_lang: Optional[str] = None         # Tesseract language(s), e.g. "eng" or "ron+eng" (default from language)
    psm: dict = field(default_factory=dict)  # PDF page -> Tesseract page segmentation mode (default 3)
    dpi: int = 300                         # render resolution for pages/, OCR and figures
    cover_page: Optional[int] = 1          # PDF page rendered as the cover image (None: no cover)
    cover_height: int = 1600               # cover image height in pixels
    figures: dict = field(default_factory=dict)  # key -> Fig; Markdown: ![Caption](figures/<key>.png)
    notes_in_print: str = "foot"           # "foot": notes at the foot of each page; "end": a list after each chapter
    brief_notes: str = ""                  # book-specific instructions added to BRIEF.md (pen marks, layouts...)
    audit_notes: str = ""                  # book-specific instructions added to AUDIT-BRIEF.md
    css: Optional[str] = None              # stylesheet path relative to the book dir (default: package scan.css)
    labels: dict = field(default_factory=dict)  # overrides for LABELS[language] ("contents", "notes")
    book_dir: str = ""                     # filled in by load_recipe

    # -- paths and labels

    def label(self, key):
        return self.labels.get(key) or LABELS.get(self.language.split("-")[0], LABELS["en"])[key]

    @property
    def out_name(self):
        name = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "-", f"{self.title} - {self.author}")
        return name.strip(" .") + ".epub"

    def path(self, *parts):
        return os.path.join(self.book_dir, *parts)

    @property
    def pdf(self):
        return self.path("source", "book.pdf")

    def md(self, part):
        return self.path("md", part.file)

    @property
    def tesseract_lang(self):
        return self.ocr_lang or OCR_LANGS.get(self.language.split("-")[0].lower())

    # -- pages

    def folio(self, n):
        """Printed page number of PDF page n as a string, or None if unnumbered."""
        if self.folios is None:
            return str(n)
        for a, b, first in self.folios:
            if a <= n <= b:
                if first is None:
                    return None
                if isinstance(first, int):
                    return str(first + n - a)
                r = to_roman(from_roman(first) + n - a)
                return r.upper() if first.isupper() else r
        return None

    def page(self, n):
        return Page(n, self.folio(n))

    def omitted(self, n):
        """Reason PDF page n is not transcribed, or None."""
        for a, b, reason in self.omit:
            if a <= n <= b:
                return reason or "omitted"
        if n == self.cover_page and not any(p.first <= n <= p.last for p in self.parts):
            return "cover"
        return None

    def part_pages(self, part):
        """PDF pages of a part that are transcribed (omitted pages drop out)."""
        return [n for n in range(part.first, part.last + 1) if not self.omitted(n)]

    def content_pages(self):
        return [n for p in self.parts for n in self.part_pages(p)]

    def expected_markers(self, part):
        return [self.page(n).marker for n in self.part_pages(part)]

    def validate(self, n_pages):
        # shape first: the checks below call folio() and omitted(), which unpack these tuples
        errors = []
        for item in self.omit:
            if not (isinstance(item, (tuple, list)) and len(item) == 3 and 1 <= item[0] <= item[1] <= n_pages):
                errors.append(f"omit {item!r}: use (first, last, reason) within 1-{n_pages}")
        for item in self.folios or []:
            if not (isinstance(item, (tuple, list)) and len(item) == 3 and 1 <= item[0] <= item[1] <= n_pages):
                errors.append(f"folios {item!r}: use (first PDF page, last PDF page, folio) within 1-{n_pages}")
                continue
            f = item[2]
            if not (f is None or (isinstance(f, int) and f >= 0)
                    or (isinstance(f, str) and from_roman(f) and (f.islower() or f.isupper()))):
                errors.append(f"folios {item!r}: the folio must be an int, a roman numeral string or None")
        if not self.parts:
            errors.append("no parts")
        if errors:
            return errors
        files = [p.file for p in self.parts]
        if len({f.lower() for f in files}) != len(files):
            errors.append("duplicate part files")
        prev = 0
        for p in self.parts:
            if not PART_FILE.fullmatch(p.file):
                errors.append(f"part file {p.file!r}: use NN-name.md (digits, '-', then letters, digits, '-', '_')")
            if not 1 <= p.first <= p.last <= n_pages:
                errors.append(f"part {p.file}: pages {p.first}-{p.last} outside 1-{n_pages}")
            if p.first <= prev:
                errors.append(f"part {p.file}: starts on page {p.first}, overlapping or before the previous part")
            prev = max(prev, p.last)
            if 1 <= p.first <= p.last <= n_pages and not self.part_pages(p):
                errors.append(f"part {p.file}: every page is omitted")
        if not errors:
            seen = {}
            for n in range(1, n_pages + 1):
                lab = self.folio(n)
                if lab is not None:
                    seen.setdefault(lab.lower(), []).append(n)
            dup = {k: v for k, v in seen.items() if len(v) > 1}
            if dup:
                errors.append("folios give the same page number to several PDF pages: "
                              + ", ".join(f"{k} (pdf {v})" for k, v in list(dup.items())[:8]))
            in_part = {n for p in self.parts for n in range(p.first, p.last + 1)}
            loose = [n for n in range(1, n_pages + 1) if n not in in_part and not self.omitted(n)]
            if loose:
                errors.append(f"pages in no part and not omitted: {ranges(loose)}")
        for key, f in self.figures.items():
            if not FIG_KEY.fullmatch(str(key)):
                errors.append(f"figure key {key!r}: use letters, digits, '-' and '_'")
            if not isinstance(f, Fig):
                errors.append(f"figure {key}: use Fig(page, box)")
                continue
            if not 1 <= f.page <= n_pages:
                errors.append(f"figure {key}: page {f.page} outside 1-{n_pages}")
            for b in (f.box, *f.erase):
                if len(b) != 4 or not (0 <= b[0] < b[2] <= 1 and 0 <= b[1] < b[3] <= 1):
                    errors.append(f"figure {key}: box {b!r} must be (x0, y0, x1, y1) fractions, x0 < x1, y0 < y1")
        if self.cover_page is not None and not 1 <= self.cover_page <= n_pages:
            errors.append(f"cover_page {self.cover_page} outside 1-{n_pages}")
        for n, deg in self.rotate.items():
            if not 1 <= n <= n_pages or deg not in (0, 90, 180, 270):
                errors.append(f"rotate {n}: {deg}: page within 1-{n_pages}, degrees 0/90/180/270")
        for n in self.psm:
            if not 1 <= n <= n_pages:
                errors.append(f"psm: page {n} outside 1-{n_pages}")
        if self.notes_in_print not in ("foot", "end"):
            errors.append(f"notes_in_print {self.notes_in_print!r}: use 'foot' or 'end'")
        if not self.tesseract_lang:
            errors.append(f"no Tesseract language known for {self.language!r}: set ocr_lang, e.g. 'fra'")
        return errors


@dataclass(frozen=True)
class Page:
    pdf: int
    folio: Optional[str]

    @property
    def name(self):
        if self.folio is None:
            return f"x-{self.pdf:03d}"
        return f"p-{self.folio.zfill(3)}" if self.folio.isdigit() else f"p-{self.folio}"

    @property
    def marker(self):
        return f"p.{self.folio}" if self.folio is not None else f"x.{self.pdf}"


ROMAN = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
         (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]


def to_roman(n):
    out = ""
    for v, s in ROMAN:
        while n >= v:
            out += s
            n -= v
    return out


def from_roman(s):
    """'xiv' -> 14; 0 when s is not a canonical roman numeral."""
    s = s.lower()
    n, i = 0, 0
    for v, sym in ROMAN:
        while s.startswith(sym, i):
            n += v
            i += len(sym)
    return n if i == len(s) and s and to_roman(n) == s else 0


def ranges(nums):
    """[1,2,3,7,9,10] -> '1-3, 7, 9-10'"""
    out = []
    for n in sorted(set(nums)):
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return ", ".join(f"{a}-{b}" if a != b else str(a) for a, b in out)


def open_checked(r):
    """Open the recipe's PDF and validate the recipe against it. -> pymupdf document"""
    import pymupdf

    if not os.path.isfile(r.pdf):
        raise SystemExit(f"missing source PDF: {r.pdf}")
    doc = pymupdf.open(r.pdf)
    errors = r.validate(len(doc))
    if errors:
        doc.close()
        raise SystemExit("recipe errors:\n  " + "\n  ".join(errors))
    return doc


def render(doc, n, dpi, deg=0, gray=True):
    """Pixmap of PDF page n at dpi, turned clockwise by deg."""
    import pymupdf

    m = pymupdf.Matrix(dpi / 72, dpi / 72).prerotate(deg)
    pix = doc[n - 1].get_pixmap(matrix=m, colorspace=pymupdf.csGRAY if gray else pymupdf.csRGB, alpha=False)
    pix.set_dpi(round(dpi), round(dpi))
    return pix


def to_pil(pix):
    from PIL import Image

    return Image.open(io.BytesIO(pix.tobytes("png")))


def looks_scanned(pdf_path, sample=12):
    """True when most sampled pages are one full-page image or carry no text."""
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        n = len(doc)
        if n == 0:
            return False
        k = min(sample, n)
        picks = sorted({round(i * (n - 1) / max(1, k - 1)) for i in range(k)})
        hits = 0
        for i in picks:
            page = doc[i]
            area = abs(page.rect)
            full = any(abs(pymupdf.Rect(im["bbox"]) & page.rect) >= 0.8 * area for im in page.get_image_info())
            if full or not page.get_text().strip():
                hits += 1
        return hits > len(picks) / 2


# ----------------------------------------------------------------- scan sheets


def sheets(r, pages_spec=None, per_sheet=24, cols=6, tile_w=300):
    """Contact sheets of PDF pages, labelled with their page names: work/sheets/sheet-NN.png"""
    from PIL import Image, ImageDraw, ImageFont

    from .diagnose import parse_pages

    out = Path(r.path("work", "sheets"))
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("sheet-*.png"):
        old.unlink()
    font = ImageFont.load_default(size=22)
    with open_checked_loose(r) as doc:
        nums = parse_pages(pages_spec, len(doc))
        written = []
        for s in range(0, len(nums), per_sheet):
            chunk = nums[s:s + per_sheet]
            tiles = []
            for n in chunk:
                deg = r.rotate.get(n, 0)
                rect = doc[n - 1].rect
                w = rect.height if deg in (90, 270) else rect.width
                pix = render(doc, n, 72 * tile_w / w, deg)
                tiles.append((n, to_pil(pix).convert("L")))
            th = max(t.height for _, t in tiles)
            rows = (len(tiles) + cols - 1) // cols
            sheet = Image.new("L", (cols * tile_w, rows * (th + 30)), 255)
            d = ImageDraw.Draw(sheet)
            for i, (n, t) in enumerate(tiles):
                x, y = (i % cols) * tile_w, (i // cols) * (th + 30)
                sheet.paste(t, (x, y + 30))
                d.rectangle((x, y + 30, x + t.width - 1, y + 30 + t.height - 1), outline=0)
                d.text((x + 5, y + 4), tile_label(r, n), fill=0, font=font)
            path = out / f"sheet-{s // per_sheet + 1:02d}.png"
            sheet.save(path, optimize=True)
            written.append((path, chunk[0], chunk[-1]))
    for path, a, b in written:
        print(f"{path}  pdf {a}-{b}")
    return written


def open_checked_loose(r):
    """Open the PDF without validating the recipe (sheets help fill it in)."""
    import pymupdf

    if not os.path.isfile(r.pdf):
        raise SystemExit(f"missing source PDF: {r.pdf}")
    return pymupdf.open(r.pdf)


def tile_label(r, n):
    parts = [f"pdf {n}"]
    if r.folios is not None:
        f = r.folio(n)
        parts.append(f"p.{f}" if f is not None else "x")
    why = r.omitted(n)
    if why:
        parts.append(why[:12])
    return " · ".join(parts)


# ----------------------------------------------------------------- scan pages


DIRS = (("pages", ".png"), ("ocr", ".txt"), ("textlayer", ".txt"))


def tesseract_langs(exe):
    p = subprocess.run([exe, "--list-langs"], capture_output=True, text=True)
    return {ln.strip() for ln in (p.stdout + p.stderr).splitlines()[1:] if ln.strip()}


def ocr_page(exe, png, lang, psm, dest):
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    p = subprocess.run([exe, str(png), "stdout", "-l", lang, "--psm", str(psm)],
                       capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise RuntimeError(f"tesseract failed on {png.name}: {p.stderr.strip()[:300]}")
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(p.stdout, encoding="utf-8")
    os.replace(tmp, dest)


def pages(r, jobs=None, force=False):
    """Render every transcribed page to work/pages/<name>.png and OCR it to work/ocr/<name>.txt.

    Incremental: work/pages.json remembers how each page was made; a page is re-rendered when
    dpi or rotation change, re-OCRed when the language or psm change, and renamed in place when
    its folio changes. Files of pages that are no longer transcribed are removed.
    """
    exe = shutil.which("tesseract")
    if not exe:
        raise SystemExit("tesseract not found (macOS: brew install tesseract tesseract-lang)")
    lang = r.tesseract_lang
    have = tesseract_langs(exe)
    missing = [x for x in (lang or "").split("+") if x not in have]
    if missing:
        raise SystemExit(f"Tesseract has no language {missing} (installed: {sorted(have)}); "
                         "macOS: brew install tesseract-lang")
    work = Path(r.path("work"))
    dirs = {k: work / k for k, _ in DIRS}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    mpath = work / "pages.json"
    manifest = json.loads(mpath.read_text()) if mpath.is_file() else {}

    with open_checked(r) as doc:
        todo = r.content_pages()
        want = {str(n): {"name": r.page(n).name, "dpi": r.dpi, "rotate": r.rotate.get(n, 0),
                         "lang": lang, "psm": r.psm.get(n, 3)} for n in todo}

        # folio changes: rename files in two phases so names can shift by one
        moves = [(manifest[k]["name"], w["name"]) for k, w in want.items()
                 if k in manifest and manifest[k]["name"] != w["name"]]
        for old, new in moves:
            for k, ext in DIRS:
                src = dirs[k] / (old + ext)
                if src.exists():
                    os.replace(src, dirs[k] / f".mv-{new}{ext}")
        for old, new in moves:
            for k, ext in DIRS:
                src = dirs[k] / f".mv-{new}{ext}"
                if src.exists():
                    os.replace(src, dirs[k] / (new + ext))
        for k in want:
            if k in manifest:
                manifest[k]["name"] = want[k]["name"]
        manifest = {k: v for k, v in manifest.items() if k in want}

        # files that belong to no transcribed page
        names = {w["name"] for w in want.values()}
        removed = 0
        for k, ext in DIRS:
            for f in dirs[k].iterdir():
                stem = f.name[:-len(ext)] if f.name.endswith(ext) else None
                generated = re.fullmatch(r"[px]-[0-9A-Za-z]+", stem or "") or f.name.startswith(".mv-") \
                    or ".tmp" in f.name
                if generated and stem not in names:
                    f.unlink()
                    removed += 1

        n_render = n_ocr = 0
        errors = []
        with ThreadPoolExecutor(max_workers=jobs or os.cpu_count() or 4) as pool:
            futures = {}
            for n in todo:
                k, w = str(n), want[str(n)]
                m = manifest.get(k) or {}
                png = dirs["pages"] / (w["name"] + ".png")
                txt = dirs["ocr"] / (w["name"] + ".txt")
                redo = force or not png.is_file() or m.get("dpi") != w["dpi"] or m.get("rotate") != w["rotate"]
                reocr = redo or not txt.is_file() or m.get("lang") != w["lang"] or m.get("psm") != w["psm"]
                if redo:
                    pix = render(doc, n, r.dpi, w["rotate"])
                    tmp = png.with_name(png.stem + ".tmp.png")
                    pix.save(str(tmp))
                    os.replace(tmp, png)
                    layer = doc[n - 1].get_text().strip()
                    tl = dirs["textlayer"] / (w["name"] + ".txt")
                    if layer:
                        tl.write_text(layer + "\n", encoding="utf-8")
                    elif tl.exists():
                        tl.unlink()
                    n_render += 1
                if reocr:
                    futures[pool.submit(ocr_page, exe, png, w["lang"], w["psm"], txt)] = k
                else:
                    manifest[k] = w
            for fut, k in futures.items():
                try:
                    fut.result()
                    manifest[k] = want[k]
                    n_ocr += 1
                except RuntimeError as e:
                    errors.append(str(e))
                    manifest.pop(k, None)
        mpath.write_text(json.dumps(dict(sorted(manifest.items(), key=lambda kv: int(kv[0]))), indent=1) + "\n")
    print(f"{len(todo)} pages: {n_render} rendered, {n_ocr} OCRed, {len(moves)} renamed, "
          f"{removed} stale files removed  ({work}/pages, ocr, textlayer)")
    if errors:
        raise SystemExit("\n".join(errors))


# ----------------------------------------------------------------- scan figures

PAD = 24       # px of white margin kept around the ink
INK = 150      # after levelling, darker than this counts as ink for trimming
MAX_W = 1400   # downscale wider crops to this width


def _px(box, w, h):
    x0, y0, x1, y1 = box
    return (round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h))


def _level(g):
    """Map the paper (the median gray) to white and stretch the ink to black."""
    hist = g.histogram()
    total, acc, paper = sum(hist), 0, 224
    for v, c in enumerate(hist):
        acc += c
        if acc > total * 0.5:
            paper = v
            break
    white, black = max(paper - 18, 46), 45
    lut = [0 if v <= black else 255 if v >= white else round((v - black) * 255 / (white - black))
           for v in range(256)]
    return g.point(lut)


def figures(r, keys=None, doc=None, quiet=False):
    """Crop recipe figures from fresh page renders into work/figures/<key>.png."""
    from PIL import Image, ImageFilter

    keys = list(r.figures) if not keys else keys
    unknown = [k for k in keys if k not in r.figures]
    if unknown:
        raise SystemExit(f"no such figures in the recipe: {unknown} (have: {list(r.figures)})")
    out = Path(r.path("work", "figures"))
    out.mkdir(parents=True, exist_ok=True)
    own = doc is None
    doc = open_checked(r) if own else doc
    try:
        cache = {}
        for key in keys:
            f = r.figures[key]
            if f.page not in cache:
                cache = {f.page: to_pil(render(doc, f.page, r.dpi, r.rotate.get(f.page, 0))).convert("L")}
            page = cache[f.page].copy()
            w, h = page.size
            for e in f.erase:
                page.paste(255, _px(e, w, h))
            g = _level(page.crop(_px(f.box, w, h)))
            # trim to the ink; a median filter on the mask drops isolated specks of dust
            mask = g.point(lambda v: 255 if v < INK else 0).filter(ImageFilter.MedianFilter(5))
            bb = mask.getbbox()
            if bb:
                x0, y0, x1, y1 = bb
                g = g.crop((max(0, x0 - PAD), max(0, y0 - PAD), min(g.width, x1 + PAD), min(g.height, y1 + PAD)))
            if g.width > MAX_W:
                g = g.resize((MAX_W, round(g.height * MAX_W / g.width)), Image.LANCZOS)
            path = out / f"{key}.png"
            g.save(path, optimize=True)
            if not quiet:
                print(f"{path}  pdf {f.page} ({r.page(f.page).marker})  {g.width}x{g.height}")
    finally:
        if own:
            doc.close()


# ----------------------------------------------------------------- scan brief

LANG_NAMES = {"en": "English", "ro": "Romanian"}


def _tmpl(name):
    return string.Template((PKG / "briefs" / name).read_text(encoding="utf-8"))


def brief(r):
    """Write work/BRIEF.md, work/AUDIT-BRIEF.md and one transcription + one audit prompt per part."""
    with open_checked(r):
        pass
    work = Path(r.path("work"))
    (work / "prompts").mkdir(parents=True, exist_ok=True)
    code = r.language.split("-")[0].lower()
    lang_file = PKG / "briefs" / f"lang-{code}.md"
    if not lang_file.is_file():
        print(f"note: no briefs/lang-{code}.md; using the English character rules. "
              "Put this language's quotes, hyphens and diacritics in brief_notes.")
        lang_file = PKG / "briefs" / "lang-en.md"
    if r.figures:
        figs = "\n".join(f"   - `figures/{k}.png`: PDF page {f.page} ({r.page(f.page).marker})"
                         for k, f in r.figures.items())
        figs = "The recipe defines these figure crops; use exactly these file names:\n\n" + figs
    else:
        figs = ("No figure crops are defined yet. Name each figure `figures/<page>-a.png` after its page "
                "name (`figures/p-012-a.png`; b, c... for more on one page) and list it in your report.")
    common = dict(
        title=r.title, author=r.author, language=LANG_NAMES.get(code, r.language), work=str(work),
        md=r.path("md"), dpi=r.dpi, figures=figs,
        brief_notes=("## Notes for this book\n\n" + r.brief_notes.strip() + "\n") if r.brief_notes.strip() else "",
        audit_notes=("## Notes for this book\n\n" + r.audit_notes.strip() + "\n") if r.audit_notes.strip() else "",
    )
    common["characters"] = string.Template(lang_file.read_text(encoding="utf-8")).substitute(common).strip()
    common["notes"] = _tmpl(f"notes-{r.notes_in_print}.md").substitute(common).strip()
    (work / "BRIEF.md").write_text(_tmpl("brief.md").substitute(common), encoding="utf-8")
    (work / "AUDIT-BRIEF.md").write_text(_tmpl("audit.md").substitute(common), encoding="utf-8")

    print(f"wrote {work}/BRIEF.md, {work}/AUDIT-BRIEF.md")
    print(f"dispatch one transcription subagent per part, with work/prompts/<part>.txt as its prompt:")
    for p in r.parts:
        nums = r.part_pages(p)
        heading = f'# {p.title} {{label="{p.label}"}}' if p.label else f"# {p.title}"
        markers = r.expected_markers(p)
        images = [str(work / "pages" / (r.page(n).name + ".png")) for n in nums]
        out = r.md(p)
        ctx = dict(common, heading=heading, part_title=p.title, first=p.first, last=p.last, n=len(nums),
                   images="\n".join(f"  {x}" for x in images),
                   markers="\n".join(f"  <!-- {m} -->" for m in markers),
                   output=out, part_notes=("Notes for this part:\n" + p.notes.strip() + "\n") if p.notes.strip() else "",
                   audit_dir=str(work / "audit" / p.stem), audit_report=str(work / "audit" / f"{p.stem}.md"))
        (work / "prompts" / f"{p.stem}.txt").write_text(_tmpl("prompt.md").substitute(ctx), encoding="utf-8")
        (work / "prompts" / f"audit-{p.stem}.txt").write_text(_tmpl("prompt-audit.md").substitute(ctx),
                                                               encoding="utf-8")
        state = "  [md exists: back it up before redoing this part]" if os.path.exists(out) else ""
        print(f"  {p.stem:28s} pdf {p.first}-{p.last} ({len(nums)} pages) -> md/{p.file}{state}")
    print(f"then: ./pdf2epub scan {os.path.basename(r.book_dir)} verify; audits use work/prompts/audit-<part>.txt")
