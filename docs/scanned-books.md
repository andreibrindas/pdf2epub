# Scanned books

A scanned PDF has no usable text layer: each page is a picture. Here the text comes from
**transcription**, not from parsing the PDF. Tesseract makes a draft of every page. Then subagents
read each page image and write clean pandoc Markdown into `books/<slug>/md/`, one file per **part**
(usually a chapter). Pandoc turns the parts into the EPUB. The engine checks every step it can: page
markers, notes, and a page-by-page word comparison against the OCR. The transcription itself is
agent work, and it is checked by independent **audits**.

The recipe is a `ScanRecipe`. The field comments in `src/pdf2epub/scan.py` (classes `ScanRecipe`,
`Part`, `Fig`) are the reference for every setting.

This route has converted English books, among them a 1971 textbook with figures and an index,
and Romanian ones. For `language="ro"` the OCR uses `ron`, the brief gives the Romanian character
rules, and the build turns cedilla ş ţ into comma-below ș ț and fixes closing quotes after „.

## Steps

0. **Setup, once per machine:** `./setup.sh`, then `brew install pandoc tesseract tesseract-lang`
   (pandoc 3.1 or newer; `tesseract --list-langs` must show the book's language).
1. **Start:** `./pdf2epub new <slug> <file.pdf> --language <code>`. It detects the scan and drafts
   a ScanRecipe. Use `--scan` if detection gets it wrong.
2. **Map the book.** Run `./pdf2epub scan <slug> sheets` and Read the contact sheets in
   `work/sheets/`. Each thumbnail is labelled with its PDF page and its page name: `p.N` for a
   printed page number, `x` for an unnumbered page. Fill every TODO in the recipe:
   - the metadata;
   - `folios`, so that the labels match the printed numbers;
   - `omit`, for blank pages, scanner or library pages, and anything not to transcribe;
   - `cover_page`;
   - `parts`, with titles from the contents page and the printed `label`;
   - `notes_in_print`;
   - `brief_notes`, for anything odd: pen marks, stamps, unusual layouts.

   For figures, set `figures` boxes, run `scan <slug> figures` and Read the crops in
   `work/figures/` until each one holds the whole drawing and nothing else (`erase` paints out pen
   marks). Re-run `sheets <pages>` to check the labels. The step is done when the recipe loads
   without errors (every page is in a part or omitted), and the sheet labels match the printed
   page numbers on every page.
3. **OCR:** `./pdf2epub scan <slug> pages`. This renders and OCRs every transcribed page into
   `work/pages/` and `work/ocr/`. It is incremental, so re-run it after any change to the recipe.
4. **Briefs:** `./pdf2epub scan <slug> brief`. This writes `work/BRIEF.md`, `work/AUDIT-BRIEF.md`,
   and two prompts per part in `work/prompts/`: `<part>.txt` and `audit-<part>.txt`.
5. **Transcribe:** start one subagent per part, a few at a time, with the full text of
   `work/prompts/<part>.txt` as its prompt. It needs image reading, so use a Claude agent. Each one
   writes `md/<part>.md` and replies with a report. Collect the reports' printed typos and
   note mismatches for NOTES.md. Before redoing a part whose md exists, back up that file, because
   the transcription is the expensive asset here.
6. **Verify:** `./pdf2epub scan <slug> verify` (or `verify <part>`). Errors are structural:
   - markers missing or out of order;
   - notes undefined or unused;
   - a part that does not open with a `# ` heading;
   - a figure file the recipe doesn't define.

   They must reach 0. Fix the warnings in the md too: a first heading without the recipe title,
   the first page marker after the heading, straight quotes. Pages scoring ≤ 0.93 against the OCR
   are flagged `<-- check`. Look at each flagged page image next to its md, and fix the md. These
   causes are harmless:
   - a figure page, where the OCR read the labels inside the drawing;
   - a note that runs onto the next page (its whole text is counted on the first page);
   - a figure placed after the paragraph it interrupts;
   - a hidden heading added in the md.

   The step is done when there are 0 errors and every flagged page has been looked at.
7. **Audit:** start one read-only subagent per part with `work/prompts/audit-<part>.txt`. Each one
   writes `work/audit/<part>.md`. A different model from the transcriber's catches more, as long
   as it can read images. An auditor that replies `NO IMAGE ACCESS` did no audit, so run that part
   again on a model that can. Auditors are advisors: check every finding against the page image
   (crop it) before you change the md. Then run `verify` again. The step is done when every
   high- and medium-certainty finding is either fixed or rejected for a reason you can state.
8. **Build:** `./pdf2epub build <slug>` writes `out/<Title - Author>.epub`. Add `--docx` or
   `--txt` for those formats too. It prints the contents list, which is one entry per h1, and
   checks the page list. Any warning must be fixed or explained in NOTES.md.
9. **Check:** `./pdf2epub check <slug>`. epubcheck must report 0 errors and 0 warnings. Then read
   two or three chapters in `out/unpacked/EPUB/text/` against their page images: headings and
   labels, notes, figures, italics.
10. **Record:** write `NOTES.md` with the book's quirks, the printed typos that were kept, and the
    reason behind each non-default recipe value. Once the user is happy with the EPUB, run
    `./pdf2epub regress <slug> --update`.

## Conventions the build relies on

- **Page markers.** `<!-- p.N -->` or `<!-- x.N -->` marks where each page starts. Markers become
  the EPUB page list, so they must be exactly the ones the prompt lists, in order. Add ` blank` for
  a blank page.
- **One h1 per printed section.** Every `# ` heading becomes a chapter file and a contents entry,
  which Kindle treats as a chapter. A heading with `{.unlisted}` stays out of the contents.
  Sub-headings are `##` and `###` and never reach the nav.
- **Notes.** Each part numbers its notes as printed, `[^N]`. The build makes the ids unique across
  the book and shows the printed number or symbol. In EPUB and text the notes become a list at the
  end of each chapter. In DOCX they become real Word footnotes.
- **Index.** Inside a `::: index` div, page numbers link to the page anchors. In
  `::: {.index .scripture}` only numbers after "p." or "pp." are pages.
- **Git.** `md/`, `work/` and `out/` stay out of git (copyrighted text). Commit only `recipe.py`,
  `NOTES.md` and `fingerprint.json`. `regress` skips a scanned book whose `md/` is incomplete on
  this machine, or when pandoc is missing. A pandoc upgrade can change the output, so when a
  scanned book changes, `regress` names the pandoc version it used. Check the diff in
  `out/unpacked/`, then accept it with `--update`.
