"""Inline text model: runs -> chunks -> XHTML.

A chunk list is a list of (text, italic, bold, sup) tuples. Two marker tuples
carry structure through the text: ("\\0PB", pdf_page) for a page break and
("\\0NOTE", n) for a footnote reference.
"""
import html
import re

PB = "\0PB"
NOTE = "\0NOTE"


class Context:
    """Per-build state shared by the pipeline stages."""

    def __init__(self, recipe, verbose=False):
        self.recipe = recipe
        self.h = recipe.heuristics
        self.verbose = verbose
        self.warnings = []
        self.note_home = {}       # note number -> section id that holds its footnote
        self.cur_section = None   # section id being rendered

    def warn(self, msg):
        self.warnings.append(msg)
        print("WARN:", msg)

    def page_label(self, pno):
        return pno + self.recipe.page_offset

    def clean_runs(self, v):
        """Runs of a visual line without decorative glyph-only fonts."""
        out = []
        for r in v.runs:
            if any(r.font.startswith(j) for j in self.recipe.junk_fonts):
                if self.verbose and r.text.strip(" "):
                    print(f"  drop junk run p{v.page}: {r.text!r} ({r.font})")
                continue
            out.append(r)
        return out


def esc(s):
    return html.escape(s, quote=False)


def attr(s):
    return html.escape(s, quote=True)


def line_chunks(ctx, v, runs=None):
    """Turn a visual line into chunks; digit superscripts become note refs."""
    runs = ctx.clean_runs(v) if runs is None else runs
    chunks = []
    for r in runs:
        t = r.text
        if r.sup:
            if chunks and chunks[-1][0] == "\0SUP":  # merge consecutive sup runs
                chunks[-1] = ("\0SUP", chunks[-1][1] + t, r.italic, r.bold)
            else:
                chunks.append(("\0SUP", t, r.italic, r.bold))
            continue
        chunks.append((t, r.italic, r.bold, False))
    out = []
    for c in chunks:
        if c[0] == "\0SUP":
            s = c[1].strip()
            if re.fullmatch(r"\d+", s) and ctx.recipe.layout.footnote_max_size > 0:
                out.append((NOTE, int(s)))
                if c[1].endswith(" "):  # keep spacing that followed the sup inside the run
                    out.append((" ", False, False, False))
            elif s:
                out.append((c[1], c[2], c[3], True))
            else:
                out.append((c[1], False, False, False))
        else:
            out.append(c)
    return out


def pb_chunks(pages):
    return [(PB, n) for n in pages]


def chunks_text(chunks):
    return "".join(c[0] for c in chunks if not c[0].startswith("\0"))


def last_token(chunks):
    t = chunks_text(chunks).rstrip()
    return t.split()[-1] if t.split() else ""


def join_lines(acc, nxt, drop_hyphen=False):
    """Append line chunks `nxt` to paragraph chunks `acc` with the right glue.

    A line-end hyphen (not a spaced dash) joins without a space; with
    drop_hyphen it is removed as a syllable break. A URL broken over lines
    joins without a space.
    """
    if not acc:
        return list(nxt)
    prev = chunks_text(acc).rstrip()
    tok = last_token(acc)
    no_space = False
    hyphen = prev.endswith("-") and not prev.endswith(" -")
    if hyphen:
        no_space = True
    elif ("http" in tok or "www" in tok) and not tok.endswith("."):
        no_space = True
    acc = rstrip_chunks(acc)
    if hyphen and drop_hyphen and len(prev) > 1 and prev[-2].isalpha():
        acc = _drop_last_char(acc)
    if not no_space:
        acc.append((" ", False, False, False))
    return acc + lstrip_chunks(list(nxt))


def _drop_last_char(chunks):
    for i in range(len(chunks) - 1, -1, -1):
        c = chunks[i]
        if not c[0].startswith("\0") and c[0]:
            chunks[i] = (c[0][:-1],) + tuple(c[1:])
            break
    return chunks


def rstrip_chunks(chunks):
    chunks = list(chunks)
    i = len(chunks) - 1
    while i >= 0:
        c = chunks[i]
        if c[0].startswith("\0"):
            i -= 1
            continue
        s = c[0].rstrip()
        chunks[i] = (s,) + tuple(c[1:])
        if s:
            break
        i -= 1
    return chunks


def lstrip_chunks(chunks):
    chunks = list(chunks)
    for i, c in enumerate(chunks):
        if c[0].startswith("\0"):
            continue
        s = c[0].lstrip()
        chunks[i] = (s,) + tuple(c[1:])
        if s:
            break
    return chunks


# a URL ends at whitespace, a tag or a quotation mark (ASCII or typographic)
URL_RE = re.compile(r"(https?://[^\s<\"„“”«»]+|www\.[^\s<\"„“”«»]+)")


def linkify(escaped_text):
    def rep(m):
        url = m.group(1)
        trail = ""
        while url and url[-1] in ".,;:)!?]'’":
            trail = url[-1] + trail
            url = url[:-1]
        href = url if url.startswith("http") else "http://" + url
        return f'<a href="{href}">{url}</a>{trail}'

    return URL_RE.sub(rep, escaped_text)


def pagebreak(ctx, pno):
    n = ctx.page_label(pno)
    return f'<span epub:type="pagebreak" role="doc-pagebreak" id="p{n}" aria-label="{n}"></span>'


def noteref(ctx, n):
    home = ctx.note_home.get(n)
    href = f"#fn{n}" if home == ctx.cur_section else f"{home}.xhtml#fn{n}"
    return (f'<sup><a epub:type="noteref" role="doc-noteref" id="fnref{n}" '
            f'href="{href}">{n}</a></sup>')


def render(ctx, chunks, notes_ok=True, link=False):
    """Chunks -> XHTML inline string. Collapses whitespace, merges styles."""
    segs = []  # (kind, payload, italic, bold, sup)
    for c in chunks:
        if c[0] == PB:
            segs.append(("pb", c[1], False, False, False))
        elif c[0] == NOTE:
            segs.append(("note", c[1], False, False, False))
        else:
            t = re.sub(r"\s+", " ", c[0].replace(" ", " "))
            if not t:
                continue
            it, bd, sp = c[1], c[2], c[3]
            if not t.strip():
                it = bd = sp = None  # whitespace takes its neighbours' style
            segs.append(("t", t, it, bd, sp))
    # collapse double spaces across boundaries
    prev_space = True
    for i, s in enumerate(segs):
        if s[0] != "t":
            continue
        t = s[1]
        if prev_space:
            t = t.lstrip(" ")
        t = re.sub(" +", " ", t)
        segs[i] = ("t", t) + s[2:]
        if t:
            prev_space = t.endswith(" ")
    # trim the end
    for i in range(len(segs) - 1, -1, -1):
        if segs[i][0] != "t":
            continue
        t = segs[i][1].rstrip(" ")
        segs[i] = ("t", t) + segs[i][2:]
        if t:
            break
    # whitespace segments inherit style when both neighbours share it
    for i, s in enumerate(segs):
        if s[0] == "t" and s[2] is None:
            prv = next((x for x in reversed(segs[:i]) if x[0] == "t" and x[2] is not None and x[1]), None)
            nxt = next((x for x in segs[i + 1:] if x[0] == "t" and x[2] is not None and x[1]), None)
            it = bool(prv and nxt and prv[2] and nxt[2])
            bd = bool(prv and nxt and prv[3] and nxt[3])
            segs[i] = ("t", s[1], it, bd, False)
    # merge adjacent same-style text segments
    merged = []
    for s in segs:
        if s[0] == "t" and not s[1]:
            continue
        if merged and s[0] == "t" and merged[-1][0] == "t" and merged[-1][2:] == s[2:]:
            merged[-1] = ("t", merged[-1][1] + s[1]) + s[2:]
        else:
            merged.append(s)
    out = []
    for s in merged:
        if s[0] == "pb":
            out.append(pagebreak(ctx, s[1]))
        elif s[0] == "note":
            out.append(noteref(ctx, s[1]) if notes_ok else f"<sup>{s[1]}</sup>")
        else:
            t = esc(s[1])
            if link:
                t = linkify(t)
            # keep spaces outside the emphasis tags
            styled = s[2] or s[3] or s[4]
            lead = " " if t.startswith(" ") and styled else ""
            trail = " " if t.endswith(" ") and styled else ""
            if lead:
                t = t[1:]
            if trail:
                t = t[:-1]
            if s[4]:
                t = f"<sup>{t}</sup>"
            if s[2]:
                t = f"<i>{t}</i>"
            if s[3]:
                t = f"<b>{t}</b>"
            out.append(lead + t + trail)
    return "".join(out)
