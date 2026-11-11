The notes are printed as a list at the end of the chapter (under a heading
   such as "Notes"). Superscript numbers in the text become pandoc references `[^N]`, placed
   exactly where the superscript is printed (usually right after a word or closing quote, before
   or after the period as printed). Do NOT reproduce the notes heading or the list as text.
   Instead, at the very end of the file, write one pandoc footnote definition per note, in order:

   ```
   [^1]: Genesis 2:18
   [^16]: John Smith, *The Title of the Book*, p. 555, Publisher, 1999.
   ```
   The page markers of the notes pages go among the definitions: on its own line just before
   the first definition that starts on that page, or, when the page begins in the middle of a
   note, inline in that note's text where the page begins (as in the body). Careful: the note number is a superscript, and the note text
   can itself begin with a digit ("2 Samuel 11:2"). Keep italics in notes. Every `[^N]` has
   exactly one definition and vice versa. If the printed book has a mismatch (a superscript with
   no note, a note never referenced, numbering that skips or repeats), don't invent anything:
   transcribe what is printed and report the mismatch.
