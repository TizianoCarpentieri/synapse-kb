# Format support

| Format | Extracted structure | Limits |
|---|---|---|
| Markdown | ATX H1–H6 outside code fences | Setext/visual headings are plain text |
| DOCX | Styles/outline levels, including inheritance | Normalized lines, not Word pages; no layout/images/notes |
| PDF | Page text with optional pypdf | No OCR; no guaranteed table geometry |
| XLSX | Sheet and row blocks, coordinates/formulas/cached values | No recalculation or merged-cell reconstruction |
| PPTX | Slides and text | No speaker notes, images or chart interpretation |
| HTML / EPUB | Headings / spine order | No external resource loading; scripts/styles excluded |
| ODT | Declared headings | Embedded objects/layout excluded |
| Python | AST classes/functions when parseable | Large/invalid code falls back to text with a warning |
| Other code/text | File and bounded text passages | No claim of language-aware symbol structure |
| Notebook | Cell source | Outputs/attachments excluded |
| Images, audio, video, legacy binaries | Listed as unavailable | No silent conversion |

Every setup must inspect `_atlas/copertura.md` or `status --json`. An index can
be successfully built while particular sources are unavailable. Do not infer
that unextracted content has been read. Headings are preserved from source
structure; they are not verified semantic labels.

The [pypdf extraction documentation](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)
explains text extraction and scan limitations. The package is pinned to the
version used in local checks; it is not presented as the latest release.
