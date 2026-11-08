# Initial format coverage

| Formats | Processing | Limits |
| --- | --- | --- |
| PDF | PyMuPDF text per page, Tesseract OCR for pages with fewer than 30 extracted characters | Mixed text/image pages may need explicit OCR preprocessing; OCR is not certified |
| PNG/JPEG/BMP/TIFF | Local Tesseract OCR; TIFF frames separately | OCR languages, handwriting and quality need validation |
| DOCX | Paragraph and table text | Headers, footers, tracked changes, comments, text boxes and embedded objects not covered |
| XLSX/XLSM | Sheet/row values and formulas; no macros executed | Charts, images, comments, embedded objects and formula cached results not covered |
| EML/MBOX/MSG | Headers and main message body | Messages containing attachments fail visibly; attachments must be exported separately |
| HTML/XML | Visible/parser text | Not a native renderer; layouts and embedded content not preserved in text |
| TXT/CSV/TSV/JSON/MD/LOG | UTF-8 text | Other encodings require conversion; CSV parsed as text |
| PST/OST, legacy DOC/XLS/PPT, PPTX, RTF, ZIP/7z, audio/video, CAD, databases, load files | Unsupported | Export/convert using validated eDiscovery tooling; failures appear in inventory |

“All eDiscovery formats” is a roadmap requirement, not a claim about this pilot. Expansion should add parser sandboxing, attachment families, load-file/Bates metadata, container limits, malware scanning, encoding detection, OCR coverage checks, audio transcription and independent test fixtures. Retain originals and document conversion provenance.

The current locator identifies a page, paragraph, row, message or text segment. There are no Bates numbers unless included in source text. DOCX and spreadsheet locators are not rendered page citations.
