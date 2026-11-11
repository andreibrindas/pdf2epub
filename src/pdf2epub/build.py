"""Recipe + PDF -> EPUB: the whole pipeline, with a classification report."""
import os

from . import epub, pdfmodel
from .blocks import build_blocks, describe, nest_headings, note_refs, parse_notes
from .inline import PB, Context
from .recipe import load_recipe
from .xhtml import section_html


def _ranges(nums):
    """[1,2,3,7,9,10] -> '1-3, 7, 9-10'"""
    out = []
    for n in sorted(set(nums)):
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return ", ".join(f"{a}-{b}" if a != b else str(a) for a, b in out)


def build(book_dir, out_dir=None, verbose=False, report=True):
    """Build books/<slug>/out/<Title - Author>.epub (and out/unpacked/). -> dict with the results."""
    recipe = load_recipe(book_dir)
    ctx = Context(recipe, verbose)
    say = print if report else (lambda *a, **k: None)
    if not os.path.isfile(recipe.pdf):
        raise SystemExit(f"missing source PDF: {recipe.pdf}")
    pages = pdfmodel.load(recipe.pdf, recipe.layout)
    errors = recipe.validate(len(pages))
    if errors:
        raise SystemExit("recipe errors:\n  " + "\n  ".join(errors))

    # footnotes, numbered through the whole book
    notes, order = parse_notes(ctx, pages)
    say(f"notes parsed: {len(notes)} (max {max(order, default=0)})")
    missing = [n for n in range(1, max(order, default=0) + 1) if n not in notes]
    if missing:
        ctx.warn(f"missing note numbers: {missing}")
    if order != sorted(order):
        ctx.warn("notes out of order")

    all_blocks = []
    refs = []
    for sec in recipe.sections:
        blocks = build_blocks(ctx, sec, pages)
        nest_headings(blocks)
        all_blocks.append((sec, blocks))
        for n in note_refs(blocks):
            refs.append(n)
            if n in ctx.note_home:
                ctx.warn(f"note {n} referenced twice")
            ctx.note_home.setdefault(n, sec.id)
    say(f"note refs in body: {len(refs)}")
    if refs != sorted(refs):
        bad = [(a, b) for a, b in zip(refs, refs[1:]) if b != a + 1]
        ctx.warn(f"note ref sequence breaks: {bad[:20]}")
    if set(refs) != set(notes):
        ctx.warn(f"refs without notes: {sorted(set(refs) - set(notes))}; "
                 f"notes without refs: {sorted(set(notes) - set(refs))}")

    # page markers: every page of every section exactly once, in reading order
    seen = [c[1] for _, blocks in all_blocks for b in blocks for c in b["chunks"] if c[0] == PB]
    expected = [p for s in recipe.sections for p in range(s.first, s.last + 1)]
    if seen != expected:
        lost = set(expected) - set(seen)
        dup = {p for p in seen if seen.count(p) > 1}
        ctx.warn("page markers differ from the section page ranges"
                 + (f"; missing: {_ranges(lost)}" if lost else "")
                 + (f"; repeated: {_ranges(dup)}" if dup else "")
                 + ("; out of order" if not lost and not dup else ""))

    # classification report: what became a heading, epigraph, table...
    for sec, blocks in all_blocks:
        say(f"\n== {sec.id} ({sec.title})")
        rows, n_par = describe(blocks)
        for kind, txt in rows:
            say(f"   {kind:9s} {txt[:90]}")
        say(f"   ({n_par} paragraphs)")

    rendered = [(sec, *section_html(ctx, sec, blocks, notes)) for sec, blocks in all_blocks]
    page_items = sorted((ctx.page_label(p), sec.id)
                        for sec, blocks in all_blocks for b in blocks for c in b["chunks"]
                        if c[0] == PB for p in [c[1]])
    files = epub.package(ctx, pages, rendered, page_items)
    out = epub.write(files, out_dir or recipe.path("out"), recipe.out_name)
    return {"epub": out, "warnings": ctx.warnings, "notes": len(notes), "refs": len(refs),
            "pages": len(page_items), "recipe": recipe}
