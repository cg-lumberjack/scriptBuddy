# samples

Real screenplays, kept as parser fixtures.

A screenplay PDF is the hard input this library exists to handle, and it cannot be
faked convincingly. Margin bands vary by template, scene numbers sit in both margins,
`(MORE)` and `(CONT'D)` break dialogue across pages, and watermarks overlay the text.
A synthetic PDF written to pass the parser proves only that the parser passes its own
fixture. These are here because `tests/test_pdf_parse.py` needs documents that were
typeset by the tools real scripts actually come out of.

They are third-party works. They are widely published, they are not this project's to
license, and the MIT grant in [`../LICENSE`](../LICENSE) does not extend to them.

## Status: decided

Keeping them is deliberate. The tradeoff was weighed by the repo owner and settled on
2026-09-17; it is not an oversight and does not need re-raising.

If it ever does become a problem, the exit is cheap: delete the files, gitignore the
directory, and let the PDF tests skip when they are absent. Nothing outside
`tests/test_pdf_parse.py` reads them.
