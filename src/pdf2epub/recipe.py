"""Book recipes.

Everything that is specific to one PDF (metadata, page ranges, page geometry,
thresholds, small hooks) lives in ``books/<slug>/recipe.py``, which defines a
module-level ``RECIPE = Recipe(...)``. The generic pipeline reads only this.
"""
from __future__ import annotations

import importlib.util
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

KINDS = {
    # kind -> default epub:type of the section's XHTML file
    "front": "preface",       # prose before chapter 1 (preface, foreword, introduction)
    "chapter": "chapter",     # numbered chapters: epigraph detection, chapter h1, sub-entries on the contents page
    "back": "afterword",      # prose after the last chapter
    "biblio": "bibliography",  # hanging-indent entries, bold lines become headings
}

# manifest ids and file stems of the pages epub.py generates; sections cannot use them
RESERVED_IDS = {"cover", "cover-image", "titlepage", "copyright", "cuprins", "nav", "ncx", "css"}

LABELS = {
    "ro": {"cover": "Copertă", "contents": "Cuprins", "landmarks": "Repere",
           "pages": "Pagini", "notes": "Note", "copyright": "Copyright"},
    "en": {"cover": "Cover", "contents": "Contents", "landmarks": "Landmarks",
           "pages": "Pages", "notes": "Notes", "copyright": "Copyright"},
}


@dataclass
class Layout:
    """Page geometry in PDF points (1/72 in). `pdf2epub inspect <book> layout` suggests values."""
    page_width: float
    left: dict                       # page parity (1 = odd, 0 = even) -> left edge of the text block
    text_width: float                # width of a full justified line
    header_max_y: float = 0.0        # drop lines whose baseline is above this (running heads)
    folio_min_y: float = float("inf")  # digit-only lines below this baseline are page numbers
    folio_size: tuple = (0.0, 99.0)  # (min, max) font size of page numbers, exclusive
    footnote_max_size: float = 0.0   # lines smaller than this are footnotes (0 = no footnotes)


@dataclass
class Heuristics:
    """Thresholds for turning visual lines into paragraphs and headings. Defaults fit a
    typical justified book with first-line indents; tune per book."""
    line_pitch: float = 1.252        # body baseline-to-baseline distance divided by font size
    chapter_min_size: float = 13.0   # lines at least this big are a chapter title (h1)
    para_indent: float = 10.0        # a first-line indent above this starts a paragraph
    short_line_rgap: float = 20.0    # a line ending this far before the right edge ends its paragraph
    para_gap: float = 1.3            # a vertical gap above this many pitches starts a paragraph
    biblio_gap: float = 1.2          # the same, inside "biblio" sections
    heading_max_chars: int = 90      # longer lines are never headings
    bold_ratio: float = 0.95         # share of bold characters for a line to count as bold
    italic_ratio: float = 0.7        # share of italic characters for an italic (rank 3) heading
    caps_ratio: float = 0.9          # share of capitals for an all-caps (rank 0) heading
    epigraph_italic: float = 0.6     # a chapter's first paragraph this italic becomes an epigraph
    subtitle_margin: float = 50.0    # a centred line after a chapter title, indented at least this much on both sides
    line_end_hyphen: str = "keep"    # "keep": a line-end '-' is part of the word (într-adevăr);
                                     # "drop": it is a syllable break, remove it when joining


@dataclass
class Section:
    """One XHTML file of the EPUB: a contiguous PDF page range."""
    id: str                          # file name stem, e.g. "cap01"
    title: str                       # title in the navigation TOC
    first: int                       # first PDF page, 1-based
    last: int                        # last PDF page, inclusive
    kind: str = "chapter"            # see KINDS
    epub_type: Optional[str] = None  # defaults from KINDS

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"section {self.id}: unknown kind {self.kind!r} (use one of {list(KINDS)})")
        if self.epub_type is None:
            self.epub_type = KINDS[self.kind]


@dataclass
class Recipe:
    title: str
    author: str
    sections: list
    layout: Layout
    language: str = "en"
    author_file_as: Optional[str] = None   # "Last, First"
    date: Optional[str] = None
    rights: Optional[str] = None
    source: Optional[str] = None           # dc:source, e.g. "Print edition, 2023"
    id_seed: Optional[str] = None          # stable seed for the uuid5 book id (default: title + author)
    heuristics: Heuristics = field(default_factory=Heuristics)
    junk_fonts: tuple = ()                 # font-name prefixes of decorative glyphs to drop
    cover_page: Optional[int] = None       # PDF page rendered as the cover image
    cover_height: int = 1600               # cover image height in pixels
    title_page_lines: Optional[list] = None  # title on the title page, one item per line (default: TITLE upper-cased)
    copyright_page: Optional[int] = None   # PDF page whose text becomes the copyright page
    copyright_min_y: float = 0.0           # ...from this baseline down
    tables: dict = field(default_factory=dict)  # PDF page -> x that splits a two-column table
    page_offset: int = 0                   # printed page number = PDF page number + page_offset
    labels: dict = field(default_factory=dict)  # overrides for LABELS[language]
    contents_subheadings: bool = True      # list each chapter's h2 headings on the contents page
    # hook: (section, title_lines) -> (label or None, name). Splits a chapter h1 such as
    # "Chapter 1" / "Beginnings" into a small label line and the title. Default: no label.
    chapter_heading: Optional[Callable] = None
    css: Optional[str] = None              # stylesheet path relative to the book dir (default: package style.css)
    book_dir: str = ""                     # filled in by load_recipe

    def label(self, key):
        return self.labels.get(key) or LABELS.get(self.language, LABELS["en"])[key]

    @property
    def out_name(self):
        name = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "-", f"{self.title} - {self.author}")
        return name.strip(" .") + ".epub"

    def path(self, *parts):
        return os.path.join(self.book_dir, *parts)

    @property
    def pdf(self):
        return self.path("source", "book.pdf")

    def validate(self, n_pages):
        errors = []
        ids = [s.id for s in self.sections]
        if len(set(ids)) != len(ids):
            errors.append("duplicate section ids")
        prev = 0
        for s in self.sections:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", s.id):
                errors.append(f"section id {s.id!r}: use letters, digits, '-' and '_', starting with a letter")
            if s.id in RESERVED_IDS:
                errors.append(f"section id {s.id!r} is taken by a generated page (reserved: {sorted(RESERVED_IDS)})")
            if not 1 <= s.first <= s.last <= n_pages:
                errors.append(f"section {s.id}: pages {s.first}-{s.last} outside 1-{n_pages}")
            if s.first <= prev:
                errors.append(f"section {s.id}: starts on page {s.first}, overlapping the previous section")
            prev = s.last
        for p in (self.cover_page, self.copyright_page, *self.tables):
            if p is not None and not 1 <= p <= n_pages:
                errors.append(f"page {p} outside 1-{n_pages}")
        return errors


def load_recipe(book_dir):
    """Import books/<slug>/recipe.py and return its RECIPE: a Recipe (text PDF) or a ScanRecipe."""
    book_dir = os.path.abspath(book_dir)
    path = os.path.join(book_dir, "recipe.py")
    if not os.path.isfile(path):
        raise SystemExit(f"no recipe at {path}")
    spec = importlib.util.spec_from_file_location(f"recipe_{os.path.basename(book_dir).replace('-', '_')}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    recipe = getattr(mod, "RECIPE", None)
    from .scan import ScanRecipe  # imported here: scan.py imports this module

    if not isinstance(recipe, (Recipe, ScanRecipe)):
        raise SystemExit(f"{path} must define RECIPE = Recipe(...) or ScanRecipe(...)")
    recipe.book_dir = book_dir
    return recipe
