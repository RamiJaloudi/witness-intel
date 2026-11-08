from pathlib import Path
import pytest
from app.extract import extract

def test_pdf_has_page_locator(tmp_path):
    import fitz
    path = tmp_path / 'source.pdf'
    doc = fitz.open(); page = doc.new_page()
    page.insert_text((72, 72), 'Alex attended the planning meeting with Morgan on Monday.')
    doc.save(path); doc.close()
    parts = list(extract(path))
    assert parts[0][0] == 'page 1' and 'Alex attended' in parts[0][1]

def test_office_paragraph_table_and_sheet_locators(tmp_path):
    from docx import Document
    from openpyxl import Workbook
    word = Document(); word.add_paragraph('Morgan attended.')
    word.add_table(rows=1, cols=1).cell(0, 0).text = 'Alex reported.'
    path = tmp_path / 'source.docx'; word.save(path)
    assert any('Morgan' in text for _, text in extract(path))
    assert any(locator == 'table 1 row 1' for locator, _ in extract(path))
    wb = Workbook(); wb.active.append(['Witness', 'Alex']); path = tmp_path / 'source.xlsx'; wb.save(path)
    assert list(extract(path)) == [('Sheet row 1', 'Witness | Alex')]

def test_email_attachments_and_unsupported_formats_fail_visibly(tmp_path):
    from email.message import EmailMessage
    msg = EmailMessage(); msg['From'] = 'synthetic@example.com'; msg.set_content('Alex attended.')
    msg.add_attachment(b'attachment', maintype='application', subtype='octet-stream', filename='evidence.bin')
    path = tmp_path / 'source.eml'; path.write_bytes(msg.as_bytes())
    with pytest.raises(ValueError, match='attachments'): list(extract(path))
    path = tmp_path / 'source.pst'; path.write_bytes(b'fake')
    with pytest.raises(ValueError, match='Unsupported'): list(extract(path))
