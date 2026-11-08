from pathlib import Path
from email import policy
from email.parser import BytesParser
import csv
import io

def extract(path: Path):
    """Yield (locator, text). Never execute macros, scripts, or embedded instructions."""
    suffix = path.suffix.lower()
    if suffix == '.pdf':
        import fitz
        from PIL import Image
        import pytesseract
        with fitz.open(path) as doc:
            for i, page in enumerate(doc):
                text = page.get_text()
                if len(text.strip()) < 30:
                    pix = page.get_pixmap(dpi=200)
                    text = pytesseract.image_to_string(Image.open(io.BytesIO(pix.tobytes('png'))))
                yield f'page {i+1}', text
    elif suffix in {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp'}:
        from PIL import Image, ImageSequence
        import pytesseract
        with Image.open(path) as image:
            for i, frame in enumerate(ImageSequence.Iterator(image)):
                yield f'image {i+1}', pytesseract.image_to_string(frame)
    elif suffix == '.docx':
        from docx import Document
        doc = Document(path)
        for i, p in enumerate(doc.paragraphs):
            yield f'paragraph {i+1}', p.text
        for i, table in enumerate(doc.tables):
            for j, row in enumerate(table.rows):
                yield f'table {i+1} row {j+1}', ' | '.join(c.text for c in row.cells)
    elif suffix in {'.xlsx', '.xlsm'}:
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=False, keep_links=False)
        try:
            for sheet in wb:
                for i, row in enumerate(sheet.iter_rows(values_only=True)):
                    yield f'{sheet.title} row {i+1}', ' | '.join(str(v) if v is not None else '' for v in row)
        finally:
            wb.close()
    elif suffix in {'.eml', '.mbox'}:
        if suffix == '.mbox':
            import mailbox
            box = mailbox.mbox(path, factory=lambda f: BytesParser(policy=policy.default).parse(f))
            messages = list(box)
            box.close()
        else:
            messages = [BytesParser(policy=policy.default).parsebytes(path.read_bytes())]
        for i, msg in enumerate(messages):
            headers = '\n'.join(f'{k}: {msg.get(k, "")}' for k in ['From', 'To', 'Cc', 'Date', 'Subject'])
            body = msg.get_body(preferencelist=('plain', 'html'))
            text = body.get_content() if body else ''
            if body and body.get_content_type() == 'text/html':
                from bs4 import BeautifulSoup
                text = BeautifulSoup(text, 'html.parser').get_text(' ', strip=True)
            # Attachments require independent inventory; fail visibly rather than omit silently.
            if any(msg.iter_attachments()):
                raise ValueError('Email attachments present: export attachments separately before import')
            yield f'message {i+1}', headers + '\n' + text
    elif suffix == '.msg':
        import extract_msg
        with extract_msg.Message(str(path)) as msg:
            if msg.attachments:
                raise ValueError('MSG attachments present: export attachments separately before import')
            yield 'message 1', f'From: {msg.sender}\nTo: {msg.to}\nDate: {msg.date}\nSubject: {msg.subject}\n{msg.body}'
    elif suffix == '.xml':
        # Stream large eDiscovery XML instead of constructing a full BeautifulSoup DOM.
        from xml.etree.ElementTree import XMLParser
        class TextTarget:
            def __init__(self): self.parts, self.blocks, self.size = [], [], 0
            def start(self, tag, attributes): self.parts.append(' ')
            def data(self, value): self.parts.append(value); self.size += len(value)
            def end(self, tag):
                self.parts.append(' ')
                if self.size >= 64000: self.flush()
            def doctype(self, *args): raise ValueError('XML DTDs are not supported; provide a safe preservation copy')
            def flush(self):
                if self.size: self.blocks.append(''.join(self.parts))
                self.parts, self.size = [], 0
            def close(self): self.flush()
        target = TextTarget(); parser = XMLParser(target=target); block = 1
        with path.open('rb') as source:
            while raw := source.read(65536):
                parser.feed(raw)
                while target.blocks:
                    yield f'XML text block {block}', target.blocks.pop(0); block += 1
            parser.close()
            for text in target.blocks:
                yield f'XML text block {block}', text; block += 1
    elif suffix in {'.html', '.htm'}:
        from bs4 import BeautifulSoup
        yield 'text', BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser').get_text(' ', strip=True)
    elif suffix in {'.txt', '.csv', '.tsv', '.json', '.md', '.log'}:
        text = path.read_text(encoding='utf-8-sig')
        yield 'text', text
    else:
        raise ValueError(f'Unsupported format {suffix}: convert to a supported preservation copy')
