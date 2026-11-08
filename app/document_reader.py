"""Read local evidence without executing content from imported files."""
from html import escape
from pathlib import Path
import re
from urllib.parse import urlencode

from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session


def register_document_reader(app):
    from . import main as db

    def checked(session, mid, did):
        db.matter(session, mid)
        document = session.get(db.Document, did)
        if not document or document.matter_id != mid:
            raise HTTPException(404, 'Document not found')
        return document

    def original_path(mid, did):
        folder = (db.ROOT / mid / did).resolve()
        if not folder.is_relative_to(db.ROOT.resolve()) or not folder.is_dir():
            return None
        return next((p for p in folder.glob('original*') if (p.name == 'original' or p.name.startswith('original.')) and p.is_file() and p.resolve().is_relative_to(folder)), None)

    def source_quote(session, mid, did, claim_id):
        claim = session.get(db.Output, claim_id) if claim_id else None
        chunk = session.get(db.Chunk, claim.payload.get('chunk_id')) if claim else None
        if claim_id and (not claim or claim.matter_id != mid or not chunk or chunk.document_id != did):
            raise HTTPException(404, 'Citation not found in this document')
        return claim.payload.get('quote', '') if claim else ''

    @app.get('/api/matters/{mid}/documents/{did}/read', response_class=HTMLResponse)
    def read_document(mid: str, did: str, claim_id: str | None = None, page: int | None = None):
        if page is not None and page < 1:
            raise HTTPException(400, 'Invalid page')
        with Session(db.engine) as session:
            document = checked(session, mid, did)
            passages = list(session.scalars(select(db.Chunk).where(db.Chunk.document_id == did).order_by(db.Chunk.created_at, db.Chunk.id)))
            name = escape(document.name)
            quote = source_quote(session, mid, did, claim_id)
            if page is None:
                claim=session.get(db.Output,claim_id) if claim_id else None
                chunk=session.get(db.Chunk,claim.payload.get('chunk_id')) if claim else None
                locator=re.search(r'(?:page|image) (\d+)',chunk.locator) if chunk else None
                page=int(locator.group(1)) if locator else 1
            def text_markup(text):
                return escape(text).replace(escape(quote), '<mark>' + escape(quote) + '</mark>') if quote and quote in text else escape(text)
            body = ''.join(f'<section id="passage-{p.id}"><h2>{escape(p.locator)}</h2><pre>{text_markup(p.text)}</pre></section>' for p in passages)
            if not passages:
                body = '<p class="notice">No readable text is available. Download the original file to inspect it.</p>'
            if document.error:
                body = f'<p class="notice">Import notice: {escape(document.error)}</p>' + body
            preview = '<p class="notice">Original layout preview is available for PDF and image files. Download other formats to inspect their original layout.</p>'
            original = original_path(mid, did)
            if original:
                suffix = original.suffix.lower()
                try:
                    if suffix == '.pdf':
                        import fitz
                        with fitz.open(original) as pdf:
                            count = len(pdf)
                        page = min(page, max(1, count))
                        query = urlencode({'page': page, **({'claim_id': claim_id} if claim_id else {})})
                        links = ' '.join(f'<a href="?{urlencode({"page": number, **({"claim_id": claim_id} if claim_id else {})})}">{label}</a>' for label, number in [('Previous Page', page - 1), ('Next Page', page + 1)] if 1 <= number <= count)
                        preview = f'<p>Original PDF · Page {page} Of {count} · {links}</p><img class="original-page" alt="Original PDF page {page}" src="/api/matters/{mid}/documents/{did}/page-image?{query}">'
                    elif suffix in {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp'}:
                        from PIL import Image
                        with Image.open(original) as image:count=getattr(image,'n_frames',1)
                        page=min(page,count)
                        links=' '.join(f'<a href="?page={number}">{label}</a>' for label,number in [('Previous Frame',page-1),('Next Frame',page+1)] if 1<=number<=count)
                        preview = f'<p>Original Image · Frame {page} Of {count} · {links}</p><img class="original-page" alt="Original evidence image" src="/api/matters/{mid}/documents/{did}/page-image?page={page}">'
                except Exception:
                    preview = '<p class="notice">Original layout preview could not be generated. Download the original to inspect it.</p>'
            quote_notice = '<p class="notice">The cited quotation is highlighted where it matches saved text or PDF text. OCR, masking and formatting may prevent a match on the original.</p>' if quote else ''
            from .palettes import palettes,selected_palette
            tokens=next(item['tokens'] for item in palettes() if item['id']==selected_palette())
            theme=';'.join('--'+key+':'+value for key,value in tokens.items())
            return HTMLResponse(f'''<!doctype html><html lang="en" style="{theme}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{name} · Witness Intel</title><link rel="stylesheet" href="/static/document_reader.css"></head><body><header><span>Witness Intel · Document Reader</span><h1>{name}</h1><p>Saved extracted text · {len(passages)} passages · Status: {escape(document.status)}</p><a href="/api/matters/{mid}/documents/{did}/original">Download Original</a></header><main><p class="notice">Saved text includes masking or privacy edits. Original previews may contain unmasked information; they stay on this local server. Original files remain unchanged.</p>{quote_notice}<div class="reader-columns"><article><h2>Original Layout</h2>{preview}</article><article><h2>Saved Extracted Text</h2>{body}</article></div></main></body></html>''')

    @app.get('/api/matters/{mid}/documents/{did}/page-image')
    def page_image(mid: str, did: str, page: int = 1, claim_id: str | None = None):
        with Session(db.engine) as session:
            checked(session, mid, did)
            quote = source_quote(session, mid, did, claim_id)
            original = original_path(mid, did)
            if not original or page < 1:
                raise HTTPException(404, 'Original page not found')
            try:
                if original.suffix.lower() == '.pdf':
                    import fitz
                    with fitz.open(original) as pdf:
                        if page > len(pdf):
                            raise HTTPException(404, 'Page not found')
                        source = pdf[page - 1]
                        if quote:
                            for rectangle in source.search_for(quote):
                                source.add_highlight_annot(rectangle)
                        scale = min(1.5, 1800 / max(source.rect.width, source.rect.height))
                        image = source.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes('png')
                elif original.suffix.lower() in {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp'}:
                    import io
                    from PIL import Image, ImageOps
                    with Image.open(original) as source:
                        if page > getattr(source, 'n_frames', 1):
                            raise HTTPException(404, 'Image frame not found')
                        source.seek(page - 1)
                        frame = ImageOps.exif_transpose(source).convert('RGB'); frame.thumbnail((1800, 1800))
                        output = io.BytesIO(); frame.save(output, format='PNG'); image = output.getvalue()
                else:
                    raise HTTPException(415, 'Original layout preview is unavailable for this format.')
                return Response(image, media_type='image/png')
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(422, 'Unable to render this original page. Download the original instead.') from None

    @app.get('/api/matters/{mid}/documents/{did}/original')
    def original_document(mid: str, did: str):
        with Session(db.engine) as session:
            document = checked(session, mid, did)
            folder = (db.ROOT / mid / did).resolve()
            if not folder.is_relative_to(db.ROOT.resolve()) or not folder.is_dir():
                raise HTTPException(404, 'Original file is unavailable')
            original = next((p for p in folder.glob('original*') if (p.name == 'original' or p.name.startswith('original.')) and p.is_file() and p.resolve().is_relative_to(folder)), None)
            if original is None:
                raise HTTPException(404, 'Original file is unavailable')
            # Always download arbitrary evidence; never execute imported HTML in the app origin.
            return FileResponse(original, filename=Path(document.name.replace('\\', '/')).name,
                                media_type='application/octet-stream', content_disposition_type='attachment')
