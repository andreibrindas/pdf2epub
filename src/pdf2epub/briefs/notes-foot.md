The notes are printed at the foot of the page. Superscript numbers (or
   symbols) in the text become pandoc references `[^N]`, placed exactly where the superscript is
   printed (usually after punctuation: `more.[^1]`). Use the printed number or symbol as N
   (`[^1]`, `[^12]`, `[^a]`; `*` is written `[^star]`, `†` is `[^dagger]`). At the very END of
   your file, write one definition per note, in order:

   ```
   [^1]: The text of the first note, as printed ...
   [^8]: [Smith]{.smallcaps}, *op. cit.*, pp. 58, 90, 131 ff.
   ```
   A note that continues onto the next page is ONE definition: join it. A note of several
   paragraphs indents the later ones by 4 spaces. Every `[^N]` has exactly one definition and
   vice versa; each N is used once in your file. If the print has a mismatch (a superscript with
   no note, a note never referenced, a skipped or repeated number), don't invent anything:
   transcribe what is printed and report it. OCR turns superscripts into plain digits and drops
   some, so check the image.
