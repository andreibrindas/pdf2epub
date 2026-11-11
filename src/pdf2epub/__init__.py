"""pdf2epub: turn a text PDF into a clean reflowable EPUB 3, one recipe per book.

Pipeline: pdfmodel (PDF -> visual lines) -> blocks (paragraphs, headings, notes)
-> xhtml (sections) -> epub (packaging). Book-specific values live in
books/<slug>/recipe.py; see AGENTS.md.
"""
__version__ = "0.1.0"
