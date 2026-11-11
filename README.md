# pdf2epub

Turns a text PDF of a book (a print-ready file, not a scan) into a clean, reflowable EPUB 3: real paragraphs and headings, linked footnotes, a cover, a contents page, print page numbers, and navigation that Kindle splits into chapters correctly. It is built to be driven by an AI agent; the workflow is in [AGENTS.md](AGENTS.md).

## Setup

```sh
./setup.sh                 # creates .venv and installs the package (Python 3.11+)
brew install epubcheck     # optional, for ./pdf2epub check
```

## Use

```sh
./pdf2epub new my-book ~/Downloads/book.pdf --language en   # books/my-book/recipe.py, with guesses
./pdf2epub inspect my-book outline                           # look at the PDF (see inspect --help)
./pdf2epub build my-book                                     # books/my-book/out/<Title - Author>.epub
./pdf2epub check my-book                                     # epubcheck + fingerprint
./pdf2epub list
```

## Layout

```
src/pdf2epub/      the engine: PDF model, paragraph/heading/note detection, XHTML, EPUB packaging
books/<slug>/
  recipe.py        everything specific to this PDF (committed)
  NOTES.md         its quirks and the reasons for the recipe values (committed)
  fingerprint.json hashes of the accepted output, for ./pdf2epub regress (committed)
  source/book.pdf  the PDF (ignored by git)
  out/             the EPUB and an unpacked copy (ignored)
  work/            scratch: renders, dumps (ignored)
```

Book files are personal copies and stay out of the repository.
