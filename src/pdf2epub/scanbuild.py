"""Scanned books, second half: check the transcribed Markdown, then build the EPUB with pandoc.

verify(): each part's md/NN-name.md against the recipe (page markers, notes, images, quotes)
and, page by page, against its OCR. Word similarity is a coarse check: a low score points at
skipped, repeated or invented text, never at a single wrong letter (that is the audit's job).

build(): md/ -> work/build/book.md -> pandoc + scan.lua -> EPUB, then post-processed so it
behaves like the text route: one contents entry per part (Kindle chapters), a page list of the
printed page numbers, and a deterministic zip.
"""
from __future__ import annotations

import difflib
import html
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from pathlib import Path

from .epub import book_id
from .recipe import load_recipe
from .scan import PKG, ScanRecipe, figures, open_checked, render

MARKER = re.compile(r"<!--\s*([px])\.([0-9A-Za-z]+)( blank)?\s*-->")
COMMENT = re.compile(r"<!--.*?-->", re.S)
NOTE_ID = re.compile(r"\[\^([^\]\s]+)\]")              # a note reference or the start of a definition
REF = re.compile(r"\[\^([^\]\s]+)\](?!:)")
DEF = re.compile(r"(?m)^\[\^([^\]\s]+)\]:")
DEF_LINE = re.compile(r"\[\^([^\]\s]+)\]:[ \t]?(.*)")
BRACKETED = r"((?:[^\[\]]|\[[^\]]*\])*)"                 # link text, one level of [..] inside
IMAGE = re.compile(r"!\[" + BRACKETED + r"\]\(([^)\s]+)[^)]*\)")
LINK = re.compile(r"(?<!!)\[" + BRACKETED + r"\]\([^)]*\)")
ATTRS = re.compile(r"\{[^{}\n]*\}")
NOTE_LABELS = {"star": "*", "dagger": "†", "ddagger": "‡"}
DIACRITICS = str.maketrans({"ş": "ș", "ţ": "ț", "Ş": "Ș", "Ţ": "Ț"})
PART_END = "<!-- pdf2epub:part-end -->"
SIMILAR = 0.93    # pages scoring at or below this are flagged


def is_ro(r):
    return r.language.split("-")[0].lower() == "ro"


def resource_dirs(r):
    return [r.path("work"), r.path("md"), r.path()]


def select_parts(r, names):
    """Parts named by file, stem or number prefix ("02"). Empty: every part."""
    if not names:
        return list(r.parts)
    out = []
    for name in names:
        hit = [p for p in r.parts if name in (p.file, p.stem) or p.file.startswith(name + "-")]
        if not hit:
            raise SystemExit(f"no part {name!r} (parts: {', '.join(p.stem for p in r.parts)})")
        out += [p for p in hit if p not in out]
    return out


# ----------------------------------------------------------------- structure checks


def check_part(r, part):
    """Structure of one part's Markdown. -> (errors, warnings)"""
    path = r.md(part)
    if not os.path.isfile(path):
        return [f"missing: transcribe PDF pages {part.first}-{part.last} into md/{part.file}"], []
    text = Path(path).read_text(encoding="utf-8")
    err, warn = [], []

    found = [f"{k}.{v}" for k, v, _ in MARKER.findall(text)]
    want = r.expected_markers(part)
    if found != want:
        missing = [m for m in want if m not in found]
        extra = [m for m in found if m not in want]
        dup = [m for m in dict.fromkeys(found) if found.count(m) > 1]
        for what, xs in (("missing page markers", missing), ("page markers not in this part", extra),
                         ("page markers repeated", dup)):
            if xs:
                err.append(f"{what}: " + ", ".join(xs[:20]) + (" ..." if len(xs) > 20 else ""))
        if not (missing or extra or dup):
            i = next(i for i, (a, b) in enumerate(zip(found, want)) if a != b)
            err.append(f"page markers out of order: {found[i]} where {want[i]} was expected")

    for m in COMMENT.finditer(text):
        if not MARKER.fullmatch(m.group(0)):
            err.append(f"HTML comment that is not a page marker: {m.group(0)[:50]!r}")
    bare = COMMENT.sub(" ", text)
    if "<!--" in bare:
        err.append("unclosed HTML comment")

    refs, defs = REF.findall(bare), DEF.findall(bare)
    checks = (("note references without a definition", [x for x in dict.fromkeys(refs) if x not in defs]),
              ("note definitions never referenced", [x for x in dict.fromkeys(defs) if x not in refs]),
              ("notes defined twice", [x for x in dict.fromkeys(defs) if defs.count(x) > 1]),
              ("notes referenced twice", [x for x in dict.fromkeys(refs) if refs.count(x) > 1]))
    for what, xs in checks:
        if xs:
            err.append(f"{what}: " + ", ".join(f"[^{x}]" for x in xs))

    first = next((ln.strip() for ln in bare.splitlines() if ln.strip()), "")
    if not first.startswith("# "):
        err.append(f"must begin with the part's heading: # {part.title}")
    elif part.title.translate(DIACRITICS) not in first.translate(DIACRITICS):
        warn.append(f"first heading {first[:70]!r} does not contain the recipe title {part.title!r}")
    m, h = MARKER.search(text), re.search(r"(?m)^# ", text)
    if m and h and h.start() < m.start():
        warn.append(f"the first page marker <!-- {m.group(1)}.{m.group(2)} --> comes after the heading; "
                    "put it on its own line before the heading")

    for _, src in IMAGE.findall(bare):
        m = re.fullmatch(r"figures/([A-Za-z0-9_-]+)\.png", src)
        if m and m.group(1) in r.figures:
            continue
        if not any(os.path.isfile(os.path.join(d, src)) for d in resource_dirs(r)):
            err.append(f"image {src}: not a recipe figure and no such file under work/, md/ or the book folder")

    plain = ATTRS.sub("", bare)
    for ch, name in (('"', "straight double quotes"), ("'", "straight apostrophes")):
        k = plain.count(ch)
        if k:
            warn.append(f"{k} {name}: write the printed quotes (see the brief's Characters)")
    return err, warn


# ----------------------------------------------------------------- similarity with the OCR


def split_defs(text):
    """Pull pandoc note definitions out of the text. -> (text without them, {id: definition text})"""
    lines = text.split("\n")
    body, defs, cur, i = [], {}, None, 0
    while i < len(lines):
        ln = lines[i]
        m = DEF_LINE.match(ln)
        if m:
            cur = m.group(1)
            defs[cur] = [m.group(2)]
        elif cur is not None and ln.strip() and not MARKER.fullmatch(ln.strip()):
            defs[cur].append(ln)              # lazy continuation or an indented paragraph
        elif cur is not None and not ln.strip():
            j = i
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and lines[j].startswith(("    ", "\t")):
                i = j                         # the definition goes on with an indented paragraph
                continue
            cur = None
            body.append(ln)
        else:
            cur = None
            body.append(ln)
        i += 1
    return "\n".join(body), {k: "\n".join(v) for k, v in defs.items()}


def page_texts(r, text):
    """-> [(marker, blank, text of that page)]. With notes at the foot of the page, each note
    definition is counted on the page of its reference, where the OCR has it."""
    text = COMMENT.sub(lambda m: m.group(0) if MARKER.fullmatch(m.group(0)) else " ", text)
    defs = {}
    if r.notes_in_print == "foot":
        text, defs = split_defs(text)
    pieces = MARKER.split(text)
    pages = [[f"{k}.{v}", bool(blank), body] for k, v, blank, body in
             (pieces[i:i + 4] for i in range(1, len(pieces), 4))]
    if pages:
        pages[0][2] = pieces[0] + "\n" + pages[0][2]
    for pg in pages:
        pg[2] += "\n" + "\n".join(defs.get(x, "") for x in REF.findall(pg[2]))
    return pages


def fold(s):
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def words(s):
    s = re.sub(r"(?<=\w)[-‐‑](?=\w)", "", s)   # "într-o", "well-known": compare without the hyphen
    return re.findall(r"[a-z]{3,}", fold(s))


def ocr_words(s):
    return words(re.sub(r"(?<=\w)[-‐‑¬­][ \t]*\n\s*(?=\w)", "", s))   # join line-end hyphenation


def md_words(s):
    s = re.sub(r"(?m)^[ \t]*:::.*$", " ", s)       # fenced div lines carry class names, not text
    s = REF.sub(" ", s)
    s = IMAGE.sub(lambda m: f" {m.group(1)} ", s)
    s = LINK.sub(lambda m: f" {m.group(1)} ", s)
    # attributes go, but a heading's printed label stays: {label="Chapter One"}
    s = ATTRS.sub(lambda m: " " + " ".join(re.findall(r'label="([^"]*)"', m.group(0))) + " ", s)
    return words(MARKER.sub(" ", s))


def score(ocr, md):
    sm = difflib.SequenceMatcher(None, ocr, md, autojunk=False)
    only_ocr, only_md = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("delete", "replace"):
            only_ocr += ocr[i1:i2]
        if tag in ("insert", "replace"):
            only_md += md[j1:j2]
    return sm.ratio(), only_ocr, only_md


def compare_part(r, part):
    """-> [(marker, line, flagged)] for each page of the part (needs `scan pages` first)."""
    work = Path(r.path("work"))
    page_of = {r.page(n).marker: r.page(n) for n in r.part_pages(part)}
    rows = []
    for marker, blank, body in page_texts(r, Path(r.md(part)).read_text(encoding="utf-8")):
        pg = page_of.get(marker)
        if pg is None:
            continue
        f = work / "ocr" / f"{pg.name}.txt"
        if not f.is_file():
            rows.append((marker, f"{marker}: no OCR (run: ./pdf2epub scan {os.path.basename(r.book_dir)} pages)", True))
            continue
        ocr, src = ocr_words(f.read_text(encoding="utf-8")), "ocr"
        tl = work / "textlayer" / f"{pg.name}.txt"
        if len(ocr) < 5 and tl.is_file():
            alt = ocr_words(tl.read_text(encoding="utf-8"))
            if len(alt) > len(ocr):
                ocr, src = alt, "textlayer"
        got = md_words(body)
        if blank:
            bad = len(ocr) > 20
            rows.append((marker, f"{marker}: marked blank, {src} has {len(ocr)} words"
                         + ("  <-- check: not blank?" if bad else ""), bad))
            continue
        if max(len(ocr), len(got)) < 5:
            rows.append((marker, f"{marker}: {src}={len(ocr)} md={len(got)} (too short to compare)", False))
            continue
        ratio, only_ocr, only_md = score(ocr, got)
        bad = ratio <= SIMILAR
        line = f"{marker}: {src}={len(ocr):4d} md={len(got):4d} sim={ratio:.3f}"
        if bad:
            line += ("  <-- check\n      only in OCR: " + " ".join(only_ocr[:12])
                     + ("..." if len(only_ocr) > 12 else "")
                     + "\n      only in md:  " + " ".join(only_md[:12]) + ("..." if len(only_md) > 12 else ""))
        rows.append((marker, line, bad))
    return rows


def verify(r, names=None, show_all=False):
    """Print the structure check and the OCR comparison of each part. -> {errors, warnings, flagged}"""
    with open_checked(r):
        pass
    errors, warnings, flagged = [], [], []
    for p in select_parts(r, names):
        print(f"== {p.file}  (pdf {p.first}-{p.last}, {len(r.part_pages(p))} pages)")
        e, w = check_part(r, p)
        for x in e:
            print(f"  ERROR {x}")
        for x in w:
            print(f"  warn  {x}")
        errors += [f"{p.file}: {x}" for x in e]
        warnings += [f"{p.file}: {x}" for x in w]
        if not os.path.isfile(r.md(p)):
            continue
        rows = compare_part(r, p)
        for marker, line, bad in rows:
            if bad:
                flagged.append(f"{p.file} {marker}")
            if bad or show_all:
                print("  " + line)
        n_bad = sum(bad for _, _, bad in rows)
        print(f"  {len(rows)} pages compared with the OCR, {n_bad} to check")
    print(f"\n{len(errors)} errors, {len(warnings)} warnings, {len(flagged)} pages to check")
    return {"errors": errors, "warnings": warnings, "flagged": flagged}


# ----------------------------------------------------------------- assembling the book


ONE = r"<!-- [px]\.[0-9A-Za-z]+(?: blank)? -->"
RUN = ONE + r"(?:[ \t]*" + ONE + r")*"     # adjacent markers are handled as one unit
MARKER_INLINE = re.compile(r"(\S?)([ \t]*)(" + RUN + r")([ \t]*)(?=(\S?))")


def tidy_markers(text):
    """'word <!-- p.N --> word' -> 'word <!-- p.N -->word': one space in the output, not two.
    Markers on their own line stay."""

    def join(m):
        left, sp1, run, sp2, right = m.groups()
        if not left or not right:
            return m.group(0)
        return left + (" " if (sp1 or sp2) else "") + re.sub(r"-->[ \t]+<!--", "--><!--", run)

    return MARKER_INLINE.sub(join, text)


def ro_quotes(text):
    """A “ that closes an open „ becomes ” (OCR and typists confuse them). Per paragraph,
    outside {attributes} and comments."""
    out = []
    for para in re.split(r"(\n[ \t]*\n)", text):
        depth = 0
        for tok in re.split(r"(\{[^{}\n]*\}|<!--.*?-->)", para, flags=re.S):
            if tok.startswith(("{", "<!--")):
                out.append(tok)
                continue
            buf = []
            for ch in tok:
                if ch == "„":
                    depth += 1
                elif ch == "”":
                    depth = max(0, depth - 1)
                elif ch == "“" and depth:
                    ch, depth = "”", depth - 1
                buf.append(ch)
            out.append("".join(buf))
    return "".join(out)


def md_escape(s):
    return re.sub(r"([\\`*_\[\]{}<>#+\-.!|~^])", r"\\\1", s)


# an own-line marker (not blank) right before a note definition, as notes printed at the end of a
# chapter are written: it moves into the definition, so the page anchor lands in the notes list
NOTES_PAGE = re.compile(r"(?m)^[ \t]*((?:<!-- [px]\.[0-9A-Za-z]+ -->[ \t]*)+)\n(?:[ \t]*\n)*"
                        r"(\[\^[^\]\s]+\]: \[[^\n]*?\]\{\.fnlabel\} )")


def prepare_part(r, i, part):
    """One part's Markdown as it goes into book.md."""
    text = Path(r.md(part)).read_text(encoding="utf-8")
    if is_ro(r):
        text = ro_quotes(text.translate(DIACRITICS))
    # note ids unique in the book; each definition starts with its printed label for scan.lua
    text = NOTE_ID.sub(lambda m: f"[^f{i}-{m.group(1)}]", text)
    text = re.sub(rf"(?m)^\[\^f{i}-([^\]\s]+)\]:[ \t]*",
                  lambda m: f"[^f{i}-{m.group(1)}]: [{md_escape(NOTE_LABELS.get(m.group(1), m.group(1)))}]"
                            "{.fnlabel} ", text)
    text = MARKER.sub(lambda m: f"<!-- {m.group(1)}.{m.group(2)}{m.group(3) or ''} -->", text)
    if r.notes_in_print == "end":
        text = NOTES_PAGE.sub(lambda m: f"{m.group(2)}{m.group(1)}", text)
    return tidy_markers(text).strip() + f"\n\n{PART_END}\n"


def metadata(r, cover):
    m = {"title": r.title, "creator": [{"role": "author", "text": r.author}], "lang": r.language,
         "identifier": [{"scheme": "UUID", "text": book_id(r)}],
         "toc-title": r.label("contents"), "notes-title": r.label("notes")}
    for key, val in (("subtitle", r.subtitle), ("publisher", r.publisher), ("date", r.date),
                     ("rights", r.rights)):
        if val:
            m[key] = val
    if cover:
        m["cover-image"] = cover
    return m


def write_cover(r, doc, path):
    n, deg = r.cover_page, r.rotate.get(r.cover_page, 0)
    rect = doc[n - 1].rect
    h = rect.width if deg in (90, 270) else rect.height
    pix = render(doc, n, 72 * r.cover_height / h, deg, gray=False)
    Path(path).write_bytes(pix.tobytes("jpeg", jpg_quality=88))


# ----------------------------------------------------------------- pandoc


def pandoc_exe():
    exe = shutil.which("pandoc")
    if not exe:
        raise SystemExit("pandoc not found (macOS: brew install pandoc)")
    first = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout.split("\n")[0]
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", first)
    v = tuple(int(x or 0) for x in m.groups()) if m else (0, 0, 0)
    if v < (3, 1):
        raise SystemExit(f"pandoc 3.1 or newer needed, found {first!r}")
    return exe, ".".join(map(str, v))


def run_pandoc(exe, r, stage, args, out):
    cmd = [exe, "-f", "markdown", str(stage / "book.md"), "--metadata-file", str(stage / "metadata.yaml"),
           "-L", str(PKG / "scan.lua"), "--resource-path=" + os.pathsep.join(resource_dirs(r)), *args,
           "-o", str(out)]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=stage)   # cover-image is relative to it
    if p.returncode:
        raise SystemExit(f"pandoc failed ({out.name}):\n{p.stderr.strip()}")
    return [ln.strip() for ln in p.stderr.splitlines() if ln.strip()]


def reference_docx(exe, stage):
    """pandoc's default reference.docx with a page break before every Heading 1."""
    path = stage / "reference.docx"
    data = subprocess.run([exe, "--print-default-data-file", "reference.docx"], capture_output=True).stdout
    src = zipfile.ZipFile(io.BytesIO(data))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "word/styles.xml":
                body = re.sub(rb'(<w:style [^>]*w:styleId="Heading1".*?<w:pPr>)', rb"\1<w:pageBreakBefore/>",
                              body, count=1, flags=re.S)
            z.writestr(info, body)
    return path


PAGEBREAK = re.compile(r'<span\b[^>]*\bepub:type="pagebreak"[^>]*?(?:/>|>\s*</span>)')
PAGE_ID = re.compile(r'<span\b[^>]*\bid="p-([^"]+)"[^>]*\bepub:type="pagebreak"')
NOTEREF = re.compile(r'<a class="noteref"[^>]*>.*?</a>', re.S)


def finish_epub(r, src, out_dir):
    """Page list, cleaned contents, no build date, fixed zip timestamps. -> (path, info)"""
    with zipfile.ZipFile(src) as z:
        names = [i.filename for i in z.infolist()]
        files = {n: z.read(n) for n in names}
    names = ["mimetype"] + [n for n in names if n != "mimetype"]
    nav = next(n for n in names if n.endswith("nav.xhtml"))
    opf = next(n for n in names if n.endswith(".opf"))
    ncx = next((n for n in names if n.endswith(".ncx")), None)
    base = nav[:nav.rfind("/") + 1]

    entries = []
    for ch in sorted(n for n in names if re.fullmatch(re.escape(base) + r"text/ch\d+\.xhtml", n)):
        entries += [(lab, ch[len(base):]) for lab in PAGE_ID.findall(files[ch].decode("utf-8"))]
    doc = files[nav].decode("utf-8")
    doc = NOTEREF.sub("", PAGEBREAK.sub("", doc))   # heading anchors and note links copied by pandoc
    items = "\n".join(f'<li><a href="{rel}#p-{lab}">{html.escape(lab)}</a></li>' for lab, rel in entries)
    page_list = (f'<nav epub:type="page-list" id="page-list" hidden="hidden">\n'
                 f'<h2>{html.escape(r.label("pages"))}</h2>\n<ol>\n{items}\n</ol>\n</nav>\n')
    doc = doc.replace("</body>", page_list + "</body>", 1)
    files[nav] = doc.encode("utf-8")
    if ncx:   # pandoc lists its title page in the NCX only; the nav has one entry per chapter
        files[ncx] = re.sub(rb'\s*<navPoint\b[^>]*>\s*<navLabel>\s*<text>[^<]*</text>\s*</navLabel>\s*'
                            rb'<content src="[^"]*title_page\.xhtml"\s*/>\s*</navPoint>', b"", files[ncx], count=1)
    if not r.date:   # pandoc fills in the build date; a scan has no date unless the recipe sets one
        files[opf] = re.sub(rb"\s*<dc:date\b[^>]*>[^<]*</dc:date>", b"", files[opf])

    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, r.out_name)
    tmp = out + ".tmp"
    with zipfile.ZipFile(tmp, "w") as z:
        for n in names:
            zi = zipfile.ZipInfo(n, date_time=(1980, 1, 1, 0, 0, 0))
            zi.external_attr = 0o644 << 16
            zi.compress_type = zipfile.ZIP_STORED if n == "mimetype" else zipfile.ZIP_DEFLATED
            z.writestr(zi, files[n])
    os.replace(tmp, out)
    unpacked = os.path.join(out_dir, "unpacked")
    shutil.rmtree(unpacked, ignore_errors=True)
    for n in names:
        p = os.path.join(unpacked, n)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(files[n])

    toc = re.search(r'<nav epub:type="toc".*?</nav>', doc, re.S)
    toc = toc.group(0) if toc else ""
    titles = [html.unescape(re.sub(r"<[^>]+>", "", t)).strip()
              for t in re.findall(r"<li\b[^>]*>\s*<a\b[^>]*>(.*?)</a>", toc, re.S)]
    ncx_doc = files[ncx].decode("utf-8") if ncx else ""
    depth = deepest = 0
    for m in re.finditer(r"<(/?)navPoint\b", ncx_doc):
        depth += -1 if m.group(1) else 1
        deepest = max(deepest, depth)
    ncx_titles = [html.unescape(x).strip() for x in re.findall(r"<navLabel>\s*<text>([^<]*)</text>", ncx_doc)]
    return out, {"toc": titles, "toc_lists": toc.count("<ol"), "ncx_depth": deepest, "ncx": ncx_titles,
                 "pages": [lab for lab, _ in entries]}


def build(book_dir, out_dir=None, report=True, formats=("epub",)):
    """Build out/<Title - Author>.epub (and .docx / .txt) from md/. -> {epub, warnings, ...}"""
    r = load_recipe(book_dir)
    if not isinstance(r, ScanRecipe):
        raise SystemExit(f"{book_dir}: not a scanned-book recipe")
    say = print if report else (lambda *a, **k: None)
    warnings = []
    doc = open_checked(r)
    try:
        errors = []
        for p in r.parts:
            e, w = check_part(r, p)
            errors += [f"{p.file}: {x}" for x in e]
            warnings += [f"{p.file}: {x}" for x in w]
        if errors:
            raise SystemExit(f"the transcription has errors (details: ./pdf2epub scan {os.path.basename(r.book_dir)} verify):\n  "
                             + "\n  ".join(errors))
        exe, version = pandoc_exe()
        stage = Path(r.path("work", "build"))
        stage.mkdir(parents=True, exist_ok=True)
        (stage / "book.md").write_text("\n\n".join(prepare_part(r, i, p) for i, p in enumerate(r.parts, 1)),
                                       encoding="utf-8")
        cover = None
        if r.cover_page:
            cover = "cover.jpg"
            write_cover(r, doc, stage / cover)
        (stage / "metadata.yaml").write_text(json.dumps(metadata(r, cover), ensure_ascii=False, indent=1) + "\n",
                                             encoding="utf-8")
        if r.figures:
            figures(r, doc=doc, quiet=True)
    finally:
        doc.close()

    out_dir = out_dir or r.path("out")
    css = r.path(r.css) if r.css else str(PKG / "scan.css")
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "pandoc.epub"
        warnings += run_pandoc(exe, r, stage, ["--toc", "--toc-depth=1", "--split-level=1", "--css", css], raw)
        epub, info = finish_epub(r, raw, out_dir)

    if info["toc_lists"] != 1 or info["ncx_depth"] > 1:
        warnings.append("the contents list is nested; Kindle would treat every entry as a chapter. "
                        "Keep sub-headings at ## and use {.unlisted} on extra # headings")
    if info["ncx"] != info["toc"]:
        warnings.append(f"the NCX lists {len(info['ncx'])} entries and the nav {len(info['toc'])}; "
                        "they should match (see the NCX in out/unpacked/)")
    expected = [r.folio(n) for n in r.content_pages() if r.folio(n) is not None]
    if info["pages"] != expected:
        lost = [x for x in expected if x not in info["pages"]]
        warnings.append(f"page list: {len(info['pages'])} anchors, expected {len(expected)}"
                        + (f"; missing {', '.join(lost[:12])}" if lost else "; order or duplicates differ"))
    stem = os.path.splitext(epub)[0]
    extra = []
    if "docx" in formats:
        ref = reference_docx(exe, stage)
        warnings += run_pandoc(exe, r, stage, ["--reference-doc", str(ref)], Path(stem + ".docx"))
        extra.append(stem + ".docx")
    if "txt" in formats:
        warnings += run_pandoc(exe, r, stage, ["-t", "plain", "--wrap=none"], Path(stem + ".txt"))
        extra.append(stem + ".txt")

    say(f"pandoc {version}")
    say(f"contents ({len(info['toc'])} entries):")
    for t in info["toc"]:
        say(f"   {t}")
    say(f"page list: {len(info['pages'])} of {len(expected)} printed pages")
    for w in warnings:
        say(f"WARN {w}")
    return {"epub": epub, "warnings": warnings, "extra": extra, "pandoc": version, **info}
