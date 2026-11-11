# Proofreading audit brief: "$title" by $author ($language)

A scanned book was transcribed into pandoc Markdown for the owner's personal e-reader. Your job is
an **independent proofreading audit** of one part of it against the page scans. You are an
auditor: **read-only.** The only files you may write are image crops in the crop folder and the
report file named in your task. Report findings only; never edit the transcription.

## Inputs

- `$work/pages/<name>.png`: $dpi dpi scan of each page. `p-NNN` = printed page N (zero-padded;
  roman numerals as printed, e.g. `p-iv`), `x-NNN` = an unnumbered page (NNN = PDF page).
  **The scan is the source of truth.** View it with the Read tool.
- `$md/NN-name.md`: the transcription you are auditing (path in your task).
- `$work/ocr/<name>.txt`: raw Tesseract OCR of the page. Use it only to locate text; it has its
  own errors and is NOT authoritative.

## Step 0: confirm you can see images

Before anything else, Read the first page image of your range and quote its first printed line
of body text. If the Read tool does not show you the image (you only get text, an error, or
nothing), STOP and reply only `NO IMAGE ACCESS` plus what you got. Never audit from the OCR
alone, and never guess what an image says.

## Step 1: read small print properly

The Read tool shows a whole page reduced in size. Footnotes, captions, tables and index numbers
are small. When anything is not crisp (a digit, a comma, a short word, italic vs roman), crop
that region at full resolution with Python/Pillow into your crop folder and Read the crop, e.g.:

    python3 -c "from PIL import Image; im=Image.open('$work/pages/p-057.png'); w,h=im.size; im.crop((0,int(h*0.75),w,h)).save('CROPS/p057-notes.png')"

Do this routinely for the notes of every page that has them. Do not report a finding you could
not actually read; list it under "could not read" instead.

$audit_notes
## How the transcription maps to pages

- `<!-- p.N -->` / `<!-- x.N -->` marks where that page's content begins. On its own line = the
  page starts with a new paragraph (or heading, list item, figure); inline mid-sentence = the
  page starts mid-paragraph. ` blank` = a blank page.
- Printed lines are unwrapped into one line per paragraph. Line-end hyphenation is joined;
  real hyphens are kept.
- Part opening: `# Title {label="Chapter One"}` stands for a printed label over the title. The
  title wording follows the book's contents page; do not report capitalisation differences
  between that title and an all-capitals print.
- `## Heading` = printed sub-heading, `### ...` = a smaller one. `*...*` = italics, `**...**` =
  bold, `[Name]{.smallcaps}` = printed small capitals.
- `::: epigraph`, `::: signature`, `::: index` ... `:::` wrap an epigraph, a signature, an index.
- Lists keep the printed numbering. `|` at line start = a line block (lines kept as broken).
  Pipe tables = printed tables. `\_\_\_\_` = printed blank lines to fill in.
- Figures: `![Caption](figures/name.png)`. The figure is an image crop, so text inside the
  drawing is NOT transcribed (do not report it). DO check the caption text against the print.
- Notes: `[^N]` where the superscript is printed; the definitions `[^N]: text` are at the END
  of the file, and a note continued on the next page is one definition.

## Intentional conventions: do NOT report these

1. Running heads and page numbers are omitted.
2. Quotation marks are typographic in the md. Do not report glyph style. DO report a quote mark
   that is missing or extra compared with the print.
3. **The author's and printer's own typos are kept exactly as printed, on purpose.** If the md
   matches a typo in the print, that is CORRECT. If the md "fixed" a printed typo, or introduced
   a typo the print does not have, REPORT it.
4. Line-break positions inside a paragraph.
5. Ellipsis "..." vs "…": report only if the number of dots differs.
6. Dash length (– vs —) and spacing around a dash: report only a dash that is missing or extra,
   or a dash that should be a hyphen (or the reverse).

## What to report

Compare every printed line of every page in your range with the md. Do not skim and do not
sample: go line by line, including notes, captions, tables and lists. Report any:

- wrong, missing, extra or reordered word or letter (wrong capitalisation, diacritics, digits in
  references, page numbers, dates)
- missing or extra punctuation (commas, periods, colons, semicolons, parentheses, quote marks)
- wrong line-end hyphen handling (a real hyphen dropped, or a line-break hyphen kept)
- missing or extra paragraph break (a printed first-line indent with no new paragraph in the md,
  or vice versa). Check the first line of every page for an indent.
- italic/bold/small caps that differ from the print
- a note reference missing, extra, misnumbered or in the wrong place; a note definition whose
  text differs from the printed note
- a heading, list number, table cell or caption that is wrong or misplaced
- a page marker in the wrong place
- anything else where the md is not a faithful transcription

## Report format

For each page, write one line: `p.N: OK` (or `x.N: OK`), or the page followed by its findings,
one per line:

    - [kind] md: «exact md text, about 5-10 words around the problem» → print: «what the scan shows» (certainty: high/medium/low)

`kind` is one of: word, letter, punct, quote, hyphen, paragraph, italic, footnote, heading,
list, table, caption, marker, other. Quote the md text exactly (copy it from the file) so it
can be found with a search.

End with a summary: number of pages checked, number of findings by certainty, and anything you
could not read clearly (with page names). Do not propose rewrites beyond the exact correction.
