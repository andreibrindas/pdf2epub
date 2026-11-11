"""Synthetic tests for the scanned-book route: no real book, no OCR run.

A tiny PDF and a hand-written transcription stand in for a scanned Romanian book. The OCR
text that `scan pages` would write is written directly, so only pandoc is needed (the build
tests are skipped without it).

Run from the repo root: .venv/bin/python -m unittest discover -s tests
"""
import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import pymupdf

from pdf2epub import scanbuild
from pdf2epub.checks import fingerprint
from pdf2epub.recipe import load_recipe
from pdf2epub.scan import Part, ScanRecipe, looks_scanned

REPO = Path(__file__).resolve().parent.parent

RECIPE = '''from pdf2epub.scan import Part, ScanRecipe

RECIPE = ScanRecipe(
    title="Carte de test", author="Ion Tester", language="ro",
    folios=[(2, 5, 1)], cover_page=1,
    parts=[
        Part("01-unu.md", "Primul capitol", 2, 4, label="Capitolul 1"),
        Part("02-doi.md", "Al doilea", 5, 5),
    ],
)
'''

# cedilla ş ţ and a “ closing „ on purpose: the build must turn them into ș ț and ”.
# p.2 starts mid-paragraph (inline marker); both parts use note id 1.
PART1 = """<!-- p.1 -->

# Primul capitol[^1] {label="Capitolul 1"}

Aceasta este prima pagină a cărţii de test, cu destule cuvinte pentru comparaţie şi un citat
„ghilimele“ la sfârşit. Al doilea rând al paragrafului spune ceva despre vreme.

Paragraful următor începe aici, merge mai departe cu câteva vorbe și se <!-- p.2 --> încheie
abia pe pagina următoare, după o pauză de pagină obișnuită.

Pagina a doua are și un paragraf întreg, scris numai ca să fie destule cuvinte de comparat.

<!-- p.3 -->

## O subsecțiune

Textul paginii a treia vorbește despre altceva, cu o notă de subsol la capătul frazei.[^2]

[^1]: Nota din titlu, tipărită la subsolul primei pagini.
[^2]: A doua notă, tipărită pe pagina a treia.
"""

PART2 = """<!-- p.4 -->

# Al doilea

Ultimul capitol are o notă cu asterisc[^star] și una numerotată, așezate în același paragraf.[^1]

[^star]: Nota cu asterisc de la subsolul paginii.
[^1]: Nota numerotată din al doilea capitol.
"""

# What Tesseract would read on each page: running heads and folios included, notes at the foot.
OCR = {
    "p-001": """Capitolul 1
Primul capitol1

Aceasta este prima pagină a cărţii de test, cu destule cuvinte pentru comparaţie şi un citat
„ghilimele“ la sfârşit. Al doilea rând al paragrafului spune ceva despre vreme.

Paragraful următor începe aici, merge mai departe cu câteva vorbe și se

1 Nota din titlu, tipărită la subsolul primei pagini.
1
""",
    "p-002": """CARTE DE TEST
încheie abia pe pagina următoare, după o pauză de pagină obiș-
nuită.

Pagina a doua are și un paragraf întreg, scris numai ca să fie destule cuvinte de comparat.
2
""",
    "p-003": """CARTE DE TEST
O subsecțiune

Textul paginii a treia vorbește despre altceva, cu o notă de subsol la capătul frazei.2

2 A doua notă, tipărită pe pagina a treia.
3
""",
    "p-004": """Al doilea

Ultimul capitol are o notă cu asterisc* și una numerotată, așezate în același paragraf.1

* Nota cu asterisc de la subsolul paginii.
1 Nota numerotată din al doilea capitol.
4
""",
}


def make_pdf(path, pages, scanned=False):
    """A PDF with one line of text per page. scanned: True, or the 0-based pages that become
    only a picture of themselves."""
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page(width=420, height=595)
        page.insert_text((50, 80), text, fontsize=14)
    pictures = set(range(len(pages))) if scanned is True else set(scanned or ())
    out = pymupdf.open()
    for i, page in enumerate(doc):
        if i in pictures:
            new = out.new_page(width=page.rect.width, height=page.rect.height)
            new.insert_image(new.rect, pixmap=page.get_pixmap(dpi=72))
        else:
            out.insert_pdf(doc, from_page=i, to_page=i)
    doc.close()
    out.save(path)
    out.close()


def quiet(fn, *args, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kw)


class Book:
    """A throwaway scanned book directory: source PDF, recipe, md/ and work/ocr/."""

    def __init__(self, root):
        self.dir = Path(root) / "test-book"
        (self.dir / "source").mkdir(parents=True)
        make_pdf(self.dir / "source" / "book.pdf",
                 ["Cover", "Page one", "Page two", "Page three", "Page four"])
        (self.dir / "recipe.py").write_text(RECIPE, encoding="utf-8")
        (self.dir / "md").mkdir()
        self.write("01-unu.md", PART1)
        self.write("02-doi.md", PART2)
        (self.dir / "work" / "ocr").mkdir(parents=True)
        for name, text in OCR.items():
            self.ocr(name, text)

    def write(self, part, text):
        (self.dir / "md" / part).write_text(text, encoding="utf-8")

    def ocr(self, name, text):
        (self.dir / "work" / "ocr" / f"{name}.txt").write_text(text, encoding="utf-8")

    def recipe(self):
        return load_recipe(self.dir)


class TestRecipe(unittest.TestCase):
    def test_page_names(self):
        r = ScanRecipe(title="T", author="A", folios=[(1, 2, None), (3, 6, "iii"), (7, 9, 1)],
                       parts=[Part("01-a.md", "A", 1, 9)])
        self.assertEqual([r.page(n).name for n in (1, 3, 6, 7, 9)],
                         ["x-001", "p-iii", "p-vi", "p-001", "p-003"])
        self.assertEqual([r.page(n).marker for n in (2, 4, 8)], ["x.2", "p.iv", "p.2"])
        self.assertEqual(r.validate(9), [])

    def test_cover_is_omitted_unless_a_part_holds_it(self):
        r = ScanRecipe(title="T", author="A", cover_page=1, parts=[Part("01-a.md", "A", 2, 3)])
        self.assertEqual(r.omitted(1), "cover")
        self.assertEqual(r.validate(3), [])
        r.parts = [Part("01-a.md", "A", 1, 3)]
        self.assertIsNone(r.omitted(1))

    def test_validate_catches_gaps_and_duplicate_folios(self):
        r = ScanRecipe(title="T", author="A", cover_page=None, folios=[(1, 3, 1), (4, 5, 2)],
                       parts=[Part("01-a.md", "A", 1, 2), Part("02-b.md", "B", 4, 5)])
        errors = "\n".join(r.validate(5))
        self.assertIn("pages in no part and not omitted: 3", errors)
        self.assertIn("same page number", errors)

    def test_looks_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            text, scan = os.path.join(tmp, "text.pdf"), os.path.join(tmp, "scan.pdf")
            make_pdf(text, ["Some words on a page"] * 3)
            make_pdf(scan, ["Some words on a page"] * 3, scanned=True)
            self.assertFalse(looks_scanned(text))
            self.assertTrue(looks_scanned(scan))

    def test_looks_scanned_short_pdf_with_a_text_title_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = os.path.join(tmp, "mixed.pdf")
            make_pdf(pdf, ["Title page", "Scanned one", "Scanned two"], scanned=[1, 2])
            self.assertTrue(looks_scanned(pdf))


class TestNew(unittest.TestCase):
    def test_new_drafts_a_scan_recipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = os.path.join(tmp, "in.pdf")
            make_pdf(pdf, ["Cover", "One", "Two"], scanned=True)
            env = {**os.environ, "PDF2EPUB_BOOKS": os.path.join(tmp, "books")}
            p = subprocess.run([sys.executable, "-I", "-m", "pdf2epub", "new", "draft", pdf, "--language", "ro"],
                               capture_output=True, text=True, env=env, cwd=REPO)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("scanned", p.stdout)
            r = load_recipe(os.path.join(tmp, "books", "draft"))
            self.assertIsInstance(r, ScanRecipe)
            self.assertEqual(r.language, "ro")
            self.assertEqual(r.validate(3), [])


class TestVerify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.book = Book(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_transcription_passes(self):
        res = quiet(scanbuild.verify, self.book.recipe())
        self.assertEqual(res, {"errors": [], "warnings": [], "flagged": []})

    def test_missing_text_is_flagged(self):
        self.book.ocr("p-003", OCR["p-003"].replace(
            "cu o notă", "cu un paragraf întreg pe care transcrierea l-a sărit cu totul, o notă"))
        res = quiet(scanbuild.verify, self.book.recipe())
        self.assertEqual(res["errors"], [])
        self.assertEqual(res["flagged"], ["01-unu.md p.3"])

    def test_structure_errors(self):
        self.book.write("01-unu.md", PART1.replace("<!-- p.2 --> ", "").replace("[^2]:", "[^3]:"))
        self.book.write("02-doi.md", PART2.replace("# Al doilea", "Al doilea"))
        errors = "\n".join(quiet(scanbuild.verify, self.book.recipe())["errors"])
        self.assertIn("missing page markers: p.2", errors)
        self.assertIn("without a definition: [^2]", errors)
        self.assertIn("never referenced: [^3]", errors)
        self.assertIn("02-doi.md: must begin with the part's heading", errors)

    def test_marker_after_heading_warns(self):
        self.book.write("02-doi.md", PART2.replace("<!-- p.4 -->\n\n# Al doilea", "# Al doilea\n\n<!-- p.4 -->"))
        warnings = quiet(scanbuild.verify, self.book.recipe())["warnings"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("comes after the heading", warnings[0])

    def test_straight_quotes_warn(self):
        self.book.write("02-doi.md", PART2.replace("asterisc", '"asterisc"'))
        warnings = quiet(scanbuild.verify, self.book.recipe())["warnings"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("straight double quotes", warnings[0])


@unittest.skipUnless(shutil.which("pandoc"), "pandoc not installed")
class TestBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.book = Book(cls.tmp.name)
        cls.res = scanbuild.build(cls.book.dir, report=False)
        with zipfile.ZipFile(cls.res["epub"]) as z:
            cls.files = {n: z.read(n).decode("utf-8", "replace") for n in z.namelist()}
        cls.chapters = [cls.files[n] for n in sorted(cls.files) if re.search(r"text/ch\d+\.xhtml$", n)]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def nav(self):
        return next(v for k, v in self.files.items() if k.endswith("nav.xhtml"))

    def test_report(self):
        self.assertEqual(self.res["warnings"], [])
        self.assertEqual(os.path.basename(self.res["epub"]), "Carte de test - Ion Tester.epub")

    def test_contents_is_flat_one_entry_per_part(self):
        self.assertEqual(self.res["toc"], ["Capitolul 1: Primul capitol", "Al doilea"])
        self.assertEqual(self.res["toc_lists"], 1)
        self.assertLessEqual(self.res["ncx_depth"], 1)
        nav = self.nav()
        self.assertNotIn("noteref", nav)
        self.assertNotIn('epub:type="pagebreak"', nav)
        self.assertNotIn("subsecțiune", nav)

    def test_ncx_matches_nav(self):
        self.assertEqual(self.res["ncx"], self.res["toc"])

    def test_page_list(self):
        self.assertEqual(self.res["pages"], ["1", "2", "3", "4"])
        self.assertIn('epub:type="page-list"', self.nav())

    def test_romanian_characters(self):
        text = "".join(self.chapters)
        self.assertNotRegex(text, "[şţŞŢ]")
        self.assertIn("cărții", text)
        self.assertIn("„ghilimele”", text)

    def test_notes_are_per_chapter_with_printed_labels(self):
        body = [c for c in self.chapters if "Primul capitol" in c or "Al doilea" in c]
        self.assertEqual(len(body), 2)
        first, second = body
        self.assertEqual(first.count('epub:type="noteref"'), 2)
        self.assertEqual(second.count('epub:type="noteref"'), 2)
        self.assertIn("<sup>*</sup>", second)
        self.assertIn('epub:type="endnotes"', second)
        self.assertIn("Nota cu asterisc", second)
        self.assertNotIn("Nota cu asterisc", first)

    def test_label_and_inline_page_anchor(self):
        first = next(c for c in self.chapters if "Primul capitol" in c)
        self.assertIn('class="chapter-label"', first)
        self.assertRegex(first, r'se <span[^>]*id="p-2"[^>]*epub:type="pagebreak"[^>]*>\s*(</span>)?încheie')

    def test_deterministic(self):
        again = scanbuild.build(self.book.dir, out_dir=os.path.join(self.tmp.name, "again"), report=False)
        self.assertEqual(fingerprint(self.res["epub"]), fingerprint(again["epub"]))

    def test_docx_and_txt(self):
        res = scanbuild.build(self.book.dir, out_dir=os.path.join(self.tmp.name, "formats"), report=False,
                              formats=("epub", "docx", "txt"))
        self.assertEqual(len(res["extra"]), 2)
        txt = Path(res["extra"][1]).read_text(encoding="utf-8")
        self.assertIn("[*]", txt)
        self.assertIn("Capitolul 1", txt)
        with zipfile.ZipFile(res["extra"][0]) as z:
            self.assertIn("word/footnotes.xml", z.namelist())
            self.assertEqual(z.read("word/footnotes.xml").decode().count("<w:footnote ") - 2, 4)


END_RECIPE = '''from pdf2epub.scan import Part, ScanRecipe

RECIPE = ScanRecipe(
    title="End Notes", author="A. Tester", language="en", cover_page=None,
    folios=[(1, 2, "i"), (3, 8, 1)], notes_in_print="end",
    parts=[
        Part("01-preface.md", "Preface", 1, 2),
        Part("02-one.md", "One", 3, 7, label="1"),
        Part("03-index.md", "Index", 8, 8),
    ],
)
'''

# notes printed after the chapter, on pages 3-4, then a blank page 5
END_PARTS = {
    "01-preface.md": "<!-- p.i -->\n\n# Preface\n\nThe preface text <!-- p.ii --> goes on here.\n",
    "02-one.md": """<!-- p.1 -->

# One {label="1"}

Body text with a note.[^1] More text <!-- p.2 --> continues here with another note.[^2]

<!-- p.3 -->

[^1]: First note text.
[^2]: Second note text that runs <!-- p.4 --> onto the next page.

<!-- p.5 blank -->
""",
    "03-index.md": """<!-- p.6 -->

# Index

::: index
Preface, ii

Grace, 1, 2–4
:::

::: {.index .scripture}
*Genesis* 1:2, p. 1; *Exodus* 2, p. 3; Leviticus 4, p. 2
:::
""",
}

PAGE_LINK = re.compile(r'<a\b[^>]*href="[^"#]*#p-([^"]+)"[^>]*>([^<]*)</a>')


@unittest.skipUnless(shutil.which("pandoc"), "pandoc not installed")
class TestEndNotesAndIndex(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        book = Path(cls.tmp.name) / "end-notes"
        (book / "source").mkdir(parents=True)
        make_pdf(book / "source" / "book.pdf", [f"Page {n}" for n in range(1, 9)])
        (book / "recipe.py").write_text(END_RECIPE, encoding="utf-8")
        (book / "md").mkdir()
        for name, text in END_PARTS.items():
            (book / "md" / name).write_text(text, encoding="utf-8")
        cls.res = scanbuild.build(book, report=False)
        with zipfile.ZipFile(cls.res["epub"]) as z:
            cls.chapters = [z.read(n).decode("utf-8") for n in sorted(z.namelist())
                            if re.search(r"text/ch\d+\.xhtml$", n)]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def chapter(self, word):
        return next(c for c in self.chapters if word in c)

    def test_report(self):
        self.assertEqual(self.res["warnings"], [])
        self.assertEqual(self.res["pages"], ["i", "ii", "1", "2", "3", "4", "5", "6"])
        self.assertEqual(self.res["toc"], ["Preface", "1. One", "Index"])

    def test_notes_pages_anchor_inside_the_notes(self):
        one = self.chapter("First note text")
        body, notes = one.split('epub:type="endnotes"')
        self.assertIn('id="p-2"', body)
        self.assertNotIn('id="p-3"', body)
        notes, after = notes.split("</section>", 1)
        self.assertRegex(notes, r'id="p-3"[^<]*(</span>)?\s*First note text')
        self.assertRegex(notes, r'runs <span[^>]*id="p-4"')
        self.assertIn('id="p-5"', after)

    def test_index_links(self):
        index = self.chapter("Leviticus")
        general, scripture = index.split("Genesis")
        self.assertEqual(PAGE_LINK.findall(general), [("ii", "ii"), ("1", "1"), ("2", "2–4")])
        self.assertEqual(PAGE_LINK.findall(scripture), [("1", "1"), ("3", "3"), ("2", "2")])


if __name__ == "__main__":
    unittest.main()
