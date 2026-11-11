# pdf2epub

Turns a PDF of a book into a clean, reflowable EPUB 3: real paragraphs and headings, linked notes, a cover, a contents page, print page numbers, and navigation that Kindle splits into chapters correctly. It handles two kinds of PDF:

- **Text PDFs** (a print-ready file with a text layer) are parsed directly. Fonts, positions and sizes become headings, paragraphs and footnotes.
- **Scanned PDFs** (each page a picture), in Romanian or English, go through Tesseract OCR. Then AI agents transcribe and proofread each page against its image, and pandoc builds the EPUB, with optional DOCX and plain text.

It is built to be driven by an AI agent. The workflow is in [AGENTS.md](AGENTS.md), and for scans in [docs/scanned-books.md](docs/scanned-books.md).

## Setup

```sh
./setup.sh                                  # creates .venv and installs the package (Python 3.11+)
brew install epubcheck                      # optional, for ./pdf2epub check
brew install pandoc tesseract tesseract-lang   # scanned books only
```

## Use

```sh
./pdf2epub new my-book ~/Downloads/book.pdf --language en   # books/my-book/recipe.py, with guesses
./pdf2epub inspect my-book outline                           # text PDF: look at it (see inspect --help)
./pdf2epub scan my-book sheets                               # scanned PDF: see scan --help
./pdf2epub build my-book                                     # books/my-book/out/<Title - Author>.epub
./pdf2epub check my-book                                     # epubcheck + fingerprint
./pdf2epub list
```

## Layout

```
src/pdf2epub/      the engine: text-PDF parsing, the scan route (scan*.py, briefs/), EPUB packaging
docs/              workflows that go beyond AGENTS.md
tests/             synthetic tests, no book needed
books/<slug>/
  recipe.py        everything specific to this PDF (committed)
  NOTES.md         its quirks and the reasons for the recipe values (committed)
  fingerprint.json hashes of the accepted output, for ./pdf2epub regress (committed)
  source/book.pdf  the PDF (ignored by git)
  md/              scanned books: the transcription, one file per part (ignored)
  out/             the EPUB and an unpacked copy (ignored)
  work/            scratch: renders, OCR, briefs, audits (ignored)
```

Book files are personal copies and stay out of the repository.
