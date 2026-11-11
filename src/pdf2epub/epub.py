"""EPUB 3 packaging: cover, title, copyright and contents pages, nav, NCX, OPF, zip.

Navigation is Kindle-safe by design: Kindle (and some other readers) treat every
nav/NCX entry, at any depth, as a chapter boundary for "time left in chapter",
progress-bar ticks and chapter skipping. So nav.xhtml and toc.ncx list one entry
per section, and the detailed contents (chapters + their h2 headings) live on an
in-book page, cuprins.xhtml / contents page, placed after the copyright page.
"""
import datetime
import os
import shutil
import uuid
import zipfile

import pymupdf

from .inline import attr, esc, join_lines, line_chunks, render
from .xhtml import xhtml_doc

XHTML = "application/xhtml+xml"
CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles>
<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
</rootfiles>
</container>
"""
DEFAULT_CSS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.css")


def book_id(recipe):
    seed = recipe.id_seed or f"{recipe.title} / {recipe.author}"
    return "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def cover_files(ctx):
    r = ctx.recipe
    with pymupdf.open(r.pdf) as doc:
        page = doc[r.cover_page - 1]
        zoom = r.cover_height / page.rect.height
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        jpg = pix.tobytes("jpeg", jpg_quality=88)
        w, h = pix.width, pix.height
    lang = attr(r.language)
    xhtml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}" lang="{lang}">
<head>
<meta charset="UTF-8"/>
<title>{esc(r.label("cover"))}</title>
<meta name="viewport" content="width={w}, height={h}"/>
<style>html,body{{margin:0;padding:0;height:100%;text-align:center;}}img{{max-width:100%;max-height:100%;}}</style>
</head>
<body epub:type="cover">
<img src="images/cover.jpg" alt="{attr(r.title)} — {attr(r.author)}"/>
</body>
</html>
"""
    return jpg, xhtml


def copyright_body(ctx, pages):
    """Paragraphs from the recipe's copyright page, or the rights statement."""
    r = ctx.recipe
    paras = []
    if r.copyright_page:
        prev = None
        for v in pages[r.copyright_page - 1]["body"]:
            if v.y < r.copyright_min_y:
                continue
            if prev is None or prev.rgap > ctx.h.short_line_rgap or v.y - prev.y > 1.5 * (prev.size * ctx.h.line_pitch):
                paras.append(line_chunks(ctx, v))
            else:
                paras[-1] = join_lines(paras[-1], line_chunks(ctx, v))
            prev = v
        body = "\n".join(f"<p>{render(ctx, c)}</p>" for c in paras)
    elif r.rights:
        body = f"<p>{esc(r.rights)}</p>"
    else:
        return None
    return f"""<div class="copyright">
<p class="cp-title">{esc(r.title)}<br/><i>{esc(r.author)}</i></p>
{body}
</div>"""


def package(ctx, pages, rendered, page_items):
    """rendered: [(section, body_xhtml, [(level, id, text)], h1_label)] in reading order;
    page_items: [(printed page number, section id)] sorted.

    -> {path inside OEBPS/: bytes}
    """
    r = ctx.recipe
    files = {}
    manifest = []  # (id, href, media-type, properties)
    spine = []
    landmarks = []  # (epub:type, href, title)
    guide = []      # (type, title, href)

    def add(fid, href, data, spine_it=True, media=XHTML, props=None):
        files[href] = data if isinstance(data, bytes) else data.encode()
        manifest.append((fid, href, media, props))
        if spine_it:
            spine.append(fid)

    if r.cover_page:
        jpg, cover = cover_files(ctx)
        add("cover-image", "images/cover.jpg", jpg, False, "image/jpeg", "cover-image")
        add("cover", "cover.xhtml", cover)
        landmarks.append(("cover", "cover.xhtml", r.label("cover")))
        guide.append(("cover", r.label("cover"), "cover.xhtml"))

    lines = r.title_page_lines or [r.title.upper()]
    add("titlepage", "titlepage.xhtml", xhtml_doc(ctx, r.title, f"""<div class="titlepage">
<p class="tp-author">{esc(r.author.upper())}</p>
<h1 class="tp-title">{"<br/>".join(esc(x) for x in lines)}</h1>
</div>""", epub_type="titlepage"))

    cbody = copyright_body(ctx, pages)
    if cbody:
        add("copyright", "copyright.xhtml", xhtml_doc(ctx, r.label("copyright"), cbody, epub_type="copyright-page"))

    contents_pos = len(spine)  # the in-book contents page goes here
    for sec, body, _, _ in rendered:
        add(sec.id, f"{sec.id}.xhtml", xhtml_doc(ctx, sec.title, body, epub_type=sec.epub_type))

    # in-book contents page: every section, plus each chapter's h2 headings
    items = []
    for sec, _, entries, _ in rendered:
        li = f'<li><a href="{sec.id}.xhtml">{esc(sec.title)}</a>'
        subs = [e for e in entries if e[0] == 2] if r.contents_subheadings and sec.kind == "chapter" else []
        if subs:
            li += "\n<ol>" + "".join(f'<li><a href="{sec.id}.xhtml#{hid}">{esc(t)}</a></li>' for _, hid, t in subs) + "</ol>"
        items.append(li + "</li>")
    contents = r.label("contents")
    add("cuprins", "cuprins.xhtml", xhtml_doc(ctx, contents, f"""<section class="contents" aria-labelledby="cuprins-title">
<h1 id="cuprins-title">{esc(contents)}</h1>
<ol class="contents">
{chr(10).join(items)}
</ol>
</section>"""), spine_it=False)
    spine.insert(contents_pos, "cuprins")
    landmarks.append(("toc", "cuprins.xhtml", contents))
    guide.append(("toc", contents, "cuprins.xhtml"))

    first = next((x for x in rendered if x[0].kind == "chapter"), rendered[0])
    first_label = first[3] or first[0].title
    landmarks.append(("bodymatter", f"{first[0].id}.xhtml", first_label))
    guide.append(("text", first_label, f"{first[0].id}.xhtml"))
    for sec, _, _, _ in rendered:
        if sec.kind == "biblio":
            landmarks.append(("bibliography", f"{sec.id}.xhtml", sec.title))
            break

    lang = attr(r.language)
    nav_items = "\n".join(f'<li><a href="{sec.id}.xhtml">{esc(sec.title)}</a></li>' for sec, _, _, _ in rendered)
    lm = "\n".join(f'<li><a epub:type="{t}" href="{h}">{esc(x)}</a></li>' for t, h, x in landmarks)
    pl = "\n".join(f'<li><a href="{sid}.xhtml#p{n}">{n}</a></li>' for n, sid in page_items)
    add("nav", "nav.xhtml", f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}" lang="{lang}">
<head>
<meta charset="UTF-8"/>
<title>{esc(contents)}</title>
<link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
<nav epub:type="toc" id="toc" role="doc-toc">
<h1>{esc(contents)}</h1>
<ol class="toc">
{nav_items}
</ol>
</nav>
<nav epub:type="landmarks" id="landmarks" hidden="hidden">
<h2>{esc(r.label("landmarks"))}</h2>
<ol>
{lm}
</ol>
</nav>
<nav epub:type="page-list" id="page-list" hidden="hidden">
<h2>{esc(r.label("pages"))}</h2>
<ol>
{pl}
</ol>
</nav>
</body>
</html>
""", spine_it=False, props="nav")

    bid = book_id(r)
    points = "\n".join(
        f'<navPoint id="np{k}" playOrder="{k}"><navLabel><text>{esc(sec.title)}</text></navLabel>'
        f'<content src="{sec.id}.xhtml"/></navPoint>' for k, (sec, _, _, _) in enumerate(rendered, 1))
    targets = "".join(
        f'<pageTarget id="pt{n}" type="normal" value="{n}"><navLabel><text>{n}</text></navLabel>'
        f'<content src="{sid}.xhtml#p{n}"/></pageTarget>' for n, sid in page_items)
    page_list = f'\n<pageList><navLabel><text>{esc(r.label("pages"))}</text></navLabel>{targets}</pageList>' if page_items else ""
    add("ncx", "toc.ncx", f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="{lang}">
<head>
<meta name="dtb:uid" content="{bid}"/>
<meta name="dtb:depth" content="1"/>
<meta name="dtb:totalPageCount" content="{len(page_items)}"/>
<meta name="dtb:maxPageNumber" content="{max((n for n, _ in page_items), default=0)}"/>
</head>
<docTitle><text>{esc(r.title)}</text></docTitle>
<docAuthor><text>{esc(r.author)}</text></docAuthor>
<navMap>
{points}
</navMap>{page_list}
</ncx>
""", spine_it=False, media="application/x-dtbncx+xml")

    css = r.path(r.css) if r.css else DEFAULT_CSS
    with open(css, "rb") as f:
        add("css", "style.css", f.read(), spine_it=False, media="text/css")

    modified = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = [f'<dc:identifier id="bookid">{bid}</dc:identifier>',
            f'<dc:title id="title">{esc(r.title)}</dc:title>',
            f'<dc:creator id="author">{esc(r.author)}</dc:creator>',
            '<meta refines="#author" property="role" scheme="marc:relators">aut</meta>']
    if r.author_file_as:
        meta.append(f'<meta refines="#author" property="file-as">{esc(r.author_file_as)}</meta>')
    meta.append(f"<dc:language>{esc(r.language)}</dc:language>")
    for tag, val in (("date", r.date), ("rights", r.rights), ("source", r.source)):
        if val:
            meta.append(f"<dc:{tag}>{esc(val)}</dc:{tag}>")
    meta.append(f'<meta property="dcterms:modified">{modified}</meta>')
    if r.cover_page:
        meta.append('<meta name="cover" content="cover-image"/>')
    man_xml = "\n".join(
        f'<item id="{i}" href="{h}" media-type="{m}"' + (f' properties="{p}"' if p else "") + "/>"
        for i, h, m, p in manifest)
    spine_xml = "\n".join(f'<itemref idref="{i}"/>' for i in spine)
    guide_xml = "\n".join(f'<reference type="{t}" title="{attr(x)}" href="{h}"/>' for t, x, h in guide)
    files["content.opf"] = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="{lang}">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
{chr(10).join(meta)}
</metadata>
<manifest>
{man_xml}
</manifest>
<spine toc="ncx">
{spine_xml}
</spine>
<guide>
{guide_xml}
</guide>
</package>
""".encode()
    return files


def write(files, out_dir, name):
    """Write the .epub (mimetype first, stored) and an unpacked copy for inspection."""
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, name)
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER, compress_type=zipfile.ZIP_DEFLATED)
        for path, data in files.items():
            z.writestr("OEBPS/" + path, data, compress_type=zipfile.ZIP_DEFLATED)
    unpacked = os.path.join(out_dir, "unpacked")
    shutil.rmtree(unpacked, ignore_errors=True)
    for path, data in files.items():
        p = os.path.join(unpacked, path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
    return out
