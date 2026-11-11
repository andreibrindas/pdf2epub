# pdf2epub: playbook for agents

Converts a PDF book into a reflowable EPUB 3 that reads well on Kindle, Apple Books and Kobo. A text PDF is parsed directly. A **scanned** PDF (pages that are pictures, Romanian or English) goes through OCR and agent transcription, and follows [docs/scanned-books.md](docs/scanned-books.md) instead of the steps below. The engine in `src/pdf2epub/` is generic. Everything specific to one PDF goes in that book's **recipe**, `books/<slug>/recipe.py`. The field comments in `src/pdf2epub/recipe.py` are the reference for every recipe setting. Commands and flags: `./pdf2epub --help` and `./pdf2epub <command> --help`.

The books are the user's personal copies. Their content (`source/`, `out/`, `work/`, a scanned book's `md/` transcription, any PDF or EPUB) stays on disk and out of git, and `.gitignore` already covers it. Commit only `recipe.py`, `NOTES.md` and `fingerprint.json` for each book.

## Convert a book

0. **Setup, once per machine:** `./setup.sh`. It needs Python 3.11 or newer; epubcheck comes from `brew install epubcheck`.
1. **Start:** `./pdf2epub new <slug> <file.pdf> --language <code>`. This copies the PDF to `books/<slug>/source/book.pdf` and drafts a recipe with a guessed layout and guessed sections. If it reports a scanned PDF, switch to [docs/scanned-books.md](docs/scanned-books.md).
2. **Look before tuning.** Render the front matter and a few chapter openings with `./pdf2epub inspect <slug> render 1-8,<pages>`, then open the PNGs with Read. Use `inspect <slug> outline` to find where chapters start. Then fill every TODO in the recipe: metadata, `cover_page`, `copyright_page`, and `sections` (merge, split or rename them, and set each one's `kind`). This step is done when no TODO is left and each section's page range starts on a real chapter opening.
3. **Build:** `./pdf2epub build <slug>`. Read the classification report one section at a time. Every h1/h2/h3 must be a heading in the PDF, and every epigraph a real epigraph. Paragraph counts should be plausible for the page count. Each WARN line must be fixed, or explained in the book's NOTES.md. When a page comes out wrong, look at it with `inspect <slug> lines <page>` next to its render, change the recipe's `layout`/`heuristics`, and rebuild. The step is done when the report reads like the book's real structure and no WARN line is left unexplained.
4. **Check:** `./pdf2epub check <slug>`. epubcheck must report 0 errors and 0 warnings. Then compare two or three chapters in `books/<slug>/out/unpacked/` against their page renders: paragraphs, italics, notes and headings.
5. **Record:** write `books/<slug>/NOTES.md` with this PDF's quirks and the reason behind each non-default recipe value. Once the user is happy with the EPUB, run `./pdf2epub regress <slug> --update` to save the fingerprint.

## Changing the engine

The output of every book is pinned by `books/<slug>/fingerprint.json`, a sha256 of each file inside the EPUB. After any change under `src/`, run `./pdf2epub regress`. Each book must report `ok`, or its diff must be exactly the change you intended. Check that in `out/unpacked/` after a `build`, then accept it with `regress --update`. Books whose `source/book.pdf` is not on this machine are skipped, and so are scanned books without their `md/` or without pandoc. Say which ones went unchecked. The scan route also has synthetic tests that need no book: `.venv/bin/python -m unittest discover -s tests`.

When a PDF needs new behaviour, add a recipe field or a hook with a default that leaves every existing book unchanged. Code for one particular book does not go in the engine.

## Lessons

- **Kindle chapters.** Kindle makes every nav/NCX entry a chapter boundary. So the nav and the NCX list one entry per section, and the subheadings appear only on the in-book contents page (`cuprins.xhtml`, controlled by `contents_subheadings`). Put new navigation on that page, never in the nav.
- **Footnotes** are lines smaller than `footnote_max_size` that start with a *superscript* number. Any other small line continues the note above it. A superscript number in the body becomes the link to that note, unless `footnote_max_size=0`, in which case it stays a plain superscript. Two layouts are not supported yet: notes that start with a plain number ("1 Text", "1. Text"), and numbering that restarts in each chapter. Either one shows up as build warnings ("note continuation without a note", "missing note numbers", "notes out of order"). When that happens, add the behaviour as a recipe option, as described under *Changing the engine*, rather than ship broken links.
- **Line-end hyphens.** `inspect <slug> hyphens` lists every line that ends in a hyphen, with the next word. Use `line_end_hyphen="keep"` when they are real hyphens (Romanian *într-adevăr*), and `"drop"` when they are syllable breaks.
- **Junk fonts.** Decorative fonts (ornaments, drop caps) with no Unicode map come out as garbage characters. Find them with `inspect <slug> fonts` and add their name prefixes to `junk_fonts`.
- **Blank pages** still get a page marker, so the page list stays complete. The build warns when the markers don't match the section page ranges.
- **Bibliography** (`kind="biblio"`): a vertical gap starts a new entry. An entry that runs onto the next page joins the previous one unless that one ended in a short line. Bold lines become subheadings.
- **Tables:** a two-column layout is set by `tables={page: split_x}` and turned into an HTML table. `inspect <slug> gaps` finds the candidate pages.
- **Chapter titles** set as a label plus a name ("1." over "The Name") need the `chapter_heading` hook in the recipe. For example, `chapter_heading=lambda sec, lines: (lines[0].text.strip(), " ".join(v.text.strip() for v in lines[1:]))` uses the first title line as the label.
- **Copyright page:** the text of `copyright_page` from `copyright_min_y` down is copied as it is. Set `copyright_min_y` below any title block at the top of that page.
- **Page numbers in the EPUB** follow the printed folios. When the printed number differs from the PDF page number, set `page_offset` (`inspect <slug> labels`).
