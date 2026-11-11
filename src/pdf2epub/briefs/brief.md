# Transcription brief: "$title" by $author ($language)

Context: the user has a scanned PDF of this book and wants to read it on their e-reader. This is a
personal format conversion of a document they already have (scan → text → EPUB for their own
reading), the same job OCR software does. Nothing is published or shared.

We are turning the scan into clean pandoc Markdown, later converted to EPUB with pandoc. You are
responsible for ONE part of the book (a page range, given in your task). Work only from the files
named here and in your task.

## Inputs

Every page has a name: `p-NNN` for a page with a printed page number N (zero-padded to 3 digits;
roman numerals as printed, e.g. `p-iv`), `x-NNN` for an unnumbered page (NNN = PDF page number).

- `$work/pages/<name>.png`: $dpi dpi scan of the page. **This is the source of truth.** Read it
  with the Read tool.
- `$work/ocr/<name>.txt`: Tesseract OCR of the same page. A good draft (usually 98%+ right) but
  with errors: footnote superscripts printed as normal digits ("more.1", "Smith4"), note numbers
  fused with the note text, "1" read as "l" or "I", garbled small capitals and decorative titles,
  lost drop caps, stray characters (`|`, `:`) from rules and marks, figures turned into garbage,
  dropped italics.
- `$work/textlayer/<name>.txt`, when present: the PDF's own hidden text layer, usually an older
  OCR. A second opinion only.

$brief_notes
## Method

Go page by page. Read the image, read the OCR, and produce the corrected text by checking the OCR
line by line against the image. Do not skip any page, line, or note. Do not paraphrase,
summarise, modernise, or "improve" anything. This is a faithful transcription.

When something is small or unclear (a footnote digit, a comma, italic vs roman), crop that region
at full resolution and Read the crop, e.g.:

    python3 -c "from PIL import Image; im=Image.open('$work/pages/p-057.png'); w,h=im.size; im.crop((0,int(h*0.75),w,h)).save('/tmp/crop-p057.png')"

## Output format (pandoc Markdown), written to the output file named in your task

1. **Part opening.** Start the file with the first page's marker, then ONE level-1 heading:
   exactly the heading line given in your task. It uses the title from the book's contents page; a printed label over the
   title ("Chapter One", "3") is the `label` attribute, never part of the title:

   ```
   # The Chapter Title {label="Chapter One"}
   ```
   A part that holds several printed sections (front matter, back matter) gets the heading from
   your task first; each further printed section title in it is another `# Title` line, with
   `{.unnumbered .unlisted}` added when it should not appear in the reader's contents list.
2. **Epigraph** (a quote set under the title, with attribution):

   ```
   ::: epigraph
   *“Quote text, all in one paragraph...”*

   Author of the Quote
   :::
   ```
   Keep inner italic/roman switches as printed; several paragraphs stay separate paragraphs.
3. **Body paragraphs**: one paragraph per line-block, separated by a blank line. Unwrap the
   printed lines. A new paragraph starts where the print has a first-line indent or a drop cap
   (look carefully at the first line of every page, too). A drop-cap paragraph starts with the
   full word.
4. **Line-end hyphenation**: join words split across lines ("gener-/alizations" →
   "generalizations"), including across a page break. Keep real hyphens. When unsure, check how
   the book writes the same word elsewhere, and list remaining doubts in your report.
5. **Running heads and page numbers**: drop them.
6. **Page markers**: where the content of a page begins, insert its marker: `<!-- p.N -->` for
   page `p-NNN` (N without zero padding: `<!-- p.7 -->`, `<!-- p.iv -->`), `<!-- x.N -->` for page
   `x-NNN`. Your task lists the exact markers, in order. If the page begins with a new paragraph
   (or heading, list item, figure, table), put the marker on its own line before it, with blank
   lines around it. If it begins mid-paragraph, put it inline, e.g.
   `...the last words of one page <!-- p.7 --> and the first of the next...`; for a word split across the page
   break, put the marker right after the joined word. Blank page: the marker with ` blank` on its
   own line (`<!-- p.8 blank -->`). Every page in your range has exactly one marker, in order.
   They become the EPUB's page numbers and let us verify that no page was skipped. Write no other
   HTML comments.
7. **Notes.** $notes
8. **Italics** `*...*` wherever the print uses italics (book titles, foreign words, emphasis).
   Bold `**...**` only if really printed bold. Words in small capitals: `[Smith]{.smallcaps}`
   written in normal case. Text printed in full capitals stays in capitals.
9. **Sub-headings**: `## Heading` for the main level inside a part, keeping the printed
   capitals; `###` for a clearly smaller second level. Run-in headings (a phrase in italics or
   bold at the start of a paragraph) stay inline as `*Phrase.*`.
10. **Lists**: numbered or lettered lists as printed, as Markdown lists (`1.`, `a.`). Keep the
    printed numbering, including a list that continues from an earlier number. Nested items are
    indented 4 spaces.
11. **Block quotes**: only passages visually set apart (indented both sides, smaller type) use
    `> `. Ordinary quotations inside paragraphs stay inline. Verse or lines broken on purpose:
    pandoc line block (`| line`).
12. **Figures and drawings** (diagrams, charts, drawings, photos): do NOT transcribe the drawing.
    Put a figure placeholder where it is printed, as its own paragraph, with the printed caption
    (number and title) as its text and no other copy of the caption:

    ```
    ![Figure 4. The caption as printed](figures/fig-04.png)
    ```
    An uncaptioned drawing has empty brackets: `![](figures/fig-05.png)`. Labels inside the
    drawing are not transcribed. $figures
    In your report, list each figure with its page.
13. **Text tables** (words in rows and columns, no drawing): a pandoc pipe table. Forms with
    blank lines to fill in: transcribe the text and write each blank as `\_\_\_\_\_\_\_\_`
    (escaped underscores).
14. **Signature lines** (e.g. a name, then place and year, under a preface): a `::: signature` fenced div,
    one line per printed line using a line block (`| John Smith`).
15. **Index** (if your part is the book's index): put the entries inside `::: index` ... `:::`,
    one paragraph per entry, page numbers exactly as printed (they become links to the pages).

## Characters

$characters
- Do not "fix" the author's spelling, grammar, or typos. Only fix OCR errors. If you notice an
  apparent typo in the original print, keep it and mention it in your report.

## When done

Write the file. Then re-read your whole output once against the OCR files to check that no page,
paragraph or note is missing (each page's marker present, in order; references and definitions
match). Your final message (report) should be short: output path, pages covered, number of notes,
figures and tables found (with pages), any printed reference/note mismatches, original typos
noticed, and anything you were unsure about (with page names). Do not edit any file other than
your output file.
