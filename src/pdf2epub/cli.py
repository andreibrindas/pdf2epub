"""Command line: pdf2epub new | inspect | build | check | regress | list."""
import argparse
import math
import os
import re
import shutil
import string
import sys
import tempfile
from pathlib import Path

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent.parent  # repo root (src layout)


def books_dir():
    env = os.environ.get("PDF2EPUB_BOOKS")
    if env:
        return Path(env).resolve()
    if (ROOT / "pyproject.toml").is_file():  # running from the repo (editable install)
        return ROOT / "books"
    return Path.cwd() / "books"


def book_path(arg):
    p = Path(arg)
    if (p / "recipe.py").is_file():
        return p.resolve()
    q = books_dir() / arg
    if (q / "recipe.py").is_file():
        return q
    raise SystemExit(f"no book {arg!r} (looked for {q / 'recipe.py'})")


def all_books():
    d = books_dir()
    return sorted(p for p in d.iterdir() if (p / "recipe.py").is_file()) if d.is_dir() else []


def py(x):
    """Python literal for the recipe template."""
    if isinstance(x, float) and math.isinf(x):
        return 'float("inf")'
    return repr(x)


# ----------------------------------------------------------------- commands

def cmd_new(a):
    import pymupdf

    from .diagnose import suggest_layout, suggest_sections

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", a.slug):
        raise SystemExit("slug: lower-case letters, digits and dashes, e.g. my-book")
    src = Path(a.pdf)
    if not src.is_file():
        raise SystemExit(f"no such file: {src}")
    book = books_dir() / a.slug
    if (book / "recipe.py").exists() and not a.force:
        raise SystemExit(f"{book / 'recipe.py'} exists (use --force to overwrite it)")
    (book / "source").mkdir(parents=True, exist_ok=True)
    pdf = book / "source" / "book.pdf"
    if src.resolve() != pdf.resolve():
        shutil.copyfile(src, pdf)

    s = suggest_layout(str(pdf))
    L = s["layout"]
    secs, how = suggest_sections(str(pdf), L, s["chapter_min_size"])
    with pymupdf.open(pdf) as doc:
        meta = doc.metadata or {}
    title = (meta.get("title") or "").strip() or "TODO title"
    author = (meta.get("author") or "").strip() or "TODO author"
    width = len(str(len(secs)))
    sec_lines = "\n".join(
        f"        Section({py(f'ch{i:0{max(2, width)}d}')}, {py(t)}, {first}, {last}, kind=\"chapter\"),"
        for i, (t, first, last) in enumerate(secs, 1))
    tmpl = string.Template((PKG / "recipe.py.tmpl").read_text())
    text = tmpl.substitute(
        slug=a.slug, title=py(title), author=py(author), language=py(a.language),
        page_width=py(L.page_width), left=py(L.left), text_width=py(L.text_width),
        header_max_y=py(L.header_max_y), folio_min_y=py(L.folio_min_y), folio_size=py(L.folio_size),
        footnote_max_size=py(L.footnote_max_size), line_pitch=py(s["line_pitch"]),
        chapter_min_size=py(s["chapter_min_size"]), sections=sec_lines)
    (book / "recipe.py").write_text(text)
    print(f"created {book}/")
    print(f"  source/book.pdf  ({s['n_pages']} pages, body text {s['body_size']} pt)")
    print(f"  recipe.py        ({len(secs)} sections guessed from {how})")
    print(f"next: ./pdf2epub inspect {a.slug} outline   then edit recipe.py, then ./pdf2epub build {a.slug}")


def cmd_inspect(a):
    from . import diagnose
    from .recipe import load_recipe

    target = Path(a.target)
    if target.suffix.lower() == ".pdf" and target.is_file():
        pdf, layout, heur = str(target), None, None
        out = Path(tempfile.gettempdir()) / "pdf2epub-png" / target.stem
    else:
        book = book_path(a.target)
        r = load_recipe(book)
        pdf, layout, heur = r.pdf, r.layout, r.heuristics
        out = book / "work" / "png"
    diagnose.run(a.mode, pdf, layout, heur, a.pages, str(a.out or out))


def _fingerprint_report(book, epub):
    from .checks import compare, fingerprint, load_fingerprint

    old = load_fingerprint(book)
    if old is None:
        return None
    diffs = compare(old, fingerprint(epub))
    if diffs:
        print(f"fingerprint: {len(diffs)} files differ from fingerprint.json")
        for d in diffs:
            print("   ", d)
        print("    (intended? ./pdf2epub regress --update " + book.name + ")")
    else:
        print("fingerprint: identical to fingerprint.json")
    return not diffs


def cmd_build(a):
    from .build import build

    book = book_path(a.book)
    res = build(book, verbose=a.verbose, report=not a.quiet)
    print(f"\nwrote {res['epub']}\n{len(res['warnings'])} warnings")
    _fingerprint_report(book, res["epub"])


def cmd_check(a):
    from .checks import epubcheck
    from .recipe import load_recipe

    book = book_path(a.book)
    r = load_recipe(book)
    epub = r.path("out", r.out_name)
    if not os.path.isfile(epub):
        raise SystemExit(f"not built yet: {epub}")
    ok, out = epubcheck(epub)
    print(out)
    _fingerprint_report(book, epub)
    if ok is False:
        sys.exit(1)


def cmd_regress(a):
    from .build import build
    from .checks import compare, fingerprint, load_fingerprint, save_fingerprint

    books = [book_path(b) for b in a.books] if a.books else [
        b for b in all_books() if (b / "fingerprint.json").is_file()]
    if not books:
        print("no books with fingerprint.json (create one: ./pdf2epub regress --update <book>)")
        return
    failed = []
    for book in books:
        if not (book / "source" / "book.pdf").is_file():
            print(f"{book.name}: skipped (source/book.pdf missing)")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            res = build(book, out_dir=tmp, report=False)
            fp = fingerprint(res["epub"])
        old = load_fingerprint(book)
        if a.update:
            save_fingerprint(book, fp)
            print(f"{book.name}: fingerprint saved ({len(fp)} files, {len(res['warnings'])} warnings)")
            continue
        if old is None:
            print(f"{book.name}: no fingerprint.json (run with --update)")
            continue
        diffs = compare(old, fp)
        if diffs:
            failed.append(book.name)
            print(f"{book.name}: CHANGED")
            for d in diffs:
                print("   ", d)
        else:
            print(f"{book.name}: ok ({len(fp)} files identical)")
    if failed:
        print("\nchanged: " + ", ".join(failed) + ". Inspect with ./pdf2epub build <book> and "
              "diff out/unpacked; accept with --update.")
        sys.exit(1)


def cmd_list(a):
    from .recipe import load_recipe

    for book in all_books():
        try:
            r = load_recipe(book)
            name = f"{r.title} - {r.author}"
            built = os.path.isfile(r.path("out", r.out_name))
        except SystemExit as e:
            name, built = f"(recipe error: {e})", False
        flags = [("source" if (book / "source" / "book.pdf").is_file() else "NO SOURCE"),
                 ("built" if built else "not built"),
                 ("fingerprint" if (book / "fingerprint.json").is_file() else "no fingerprint")]
        print(f"{book.name:24s} {name}  [{', '.join(flags)}]")


# ----------------------------------------------------------------- parser

def main(argv=None):
    from .diagnose import MODES

    p = argparse.ArgumentParser(prog="pdf2epub", description="Turn a text PDF into a clean reflowable EPUB 3, "
                                "one recipe per book (books/<slug>/recipe.py).")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("new", help="start a book: copy the PDF in and draft a recipe")
    s.add_argument("slug", help="folder name under books/, e.g. my-book")
    s.add_argument("pdf")
    s.add_argument("--language", default="en", help="BCP 47 language code (default en)")
    s.add_argument("--force", action="store_true", help="overwrite an existing recipe.py")
    s.set_defaults(fn=cmd_new)

    s = sub.add_parser("inspect", help="look at the PDF: layout, outline, fonts, lines...",
                       formatter_class=argparse.RawDescriptionHelpFormatter,
                       epilog="modes:\n" + "\n".join(f"  {k:9s} {v}" for k, v in MODES.items()))
    s.add_argument("target", help="book slug/dir (uses its recipe) or a .pdf file")
    s.add_argument("mode", choices=list(MODES), metavar="mode")
    s.add_argument("pages", nargs="?", help="e.g. 12 | 12,15-17 | all (default all)")
    s.add_argument("--out", help="render: output directory")
    s.set_defaults(fn=cmd_inspect)

    s = sub.add_parser("build", help="build books/<slug>/out/<Title - Author>.epub")
    s.add_argument("book")
    s.add_argument("-v", "--verbose", action="store_true", help="also log dropped junk-font runs")
    s.add_argument("-q", "--quiet", action="store_true", help="no classification report, warnings only")
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("check", help="epubcheck the built EPUB, compare with fingerprint.json")
    s.add_argument("book")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("regress", help="rebuild books and compare with their fingerprint.json")
    s.add_argument("books", nargs="*", help="default: every book with a fingerprint.json")
    s.add_argument("--update", action="store_true", help="accept the current output as the new fingerprint")
    s.set_defaults(fn=cmd_regress)

    s = sub.add_parser("list", help="list books and their state")
    s.set_defaults(fn=cmd_list)

    a = p.parse_args(argv)
    a.fn(a)
