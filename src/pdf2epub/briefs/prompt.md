Transcribe one part of a scanned book into pandoc Markdown. The user has a scanned PDF of this
book and wants to read it on their e-reader; this is a personal format conversion of a document
they already have, the same job OCR software does. Nothing is published or shared.

First read the brief, $work/BRIEF.md, and follow it exactly.

Your part: "$part_title", PDF pages $first-$last ($n pages to transcribe).
Your file opens with the first page's marker, then exactly this heading:

$heading

Page images, in order (OCR drafts are the same names under $work/ocr/ with .txt):
$images

Page markers to write, exactly these, in this order:
$markers

Output file: $output
$part_notes
Work read-only on everything except your output file. When done, reply with the short report
described in the brief.
