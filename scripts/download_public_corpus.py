"""Download a documented public FCIC test corpus. No AI API calls or credentials used."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import fitz
import httpx

BASE = 'https://fcic-static.law.stanford.edu/cdn_media/fcic-testimony/'
DEST = Path(__file__).resolve().parents[1] / 'data' / 'public_corpora' / 'fcic'
GROUPS = {
    '01_citigroup_starter/documents': [
        '2010-0407-Bowen.pdf', '2010-0407-Bushnell.pdf',
        '2010-0407-Maheras.pdf', '2010-0407-Mills.pdf',
        '2010-0408-Prince.pdf', '2010-0408-Rubin.pdf',
        '2010-0407-hearing-excerpts.pdf', '2010-0408-hearing-excerpts.pdf',
    ],
    '02_full_hearings/documents': ['2010-0407-Transcript.pdf', '2010-0408-Transcript.pdf'],
    '03_derivatives_emails/documents': [
        '2010-0630-Cassano.pdf', '2010-0701-Viniar.pdf',
        '2010-0701-Goldman-AIG-Collateral-Call-timeline.pdf',
        '2010-0701-Athan-Frost-email.pdf', '2010-0630-Lehman-Sparks-email.pdf',
        '2010-0630-Ash-Friedman-email.pdf',
    ],
}

def describe(path):
    with fitz.open(path) as doc:
        counts = [len(page.get_text().strip()) for page in doc]
        return {'pages': len(doc), 'text_characters': sum(counts),
                'low_text_pages': [i+1 for i, n in enumerate(counts) if n < 30]}

def download(item):
    group, filename = item
    url = BASE + filename
    path = DEST / group / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            response = httpx.get(url, follow_redirects=True, timeout=45, trust_env=False)
            response.raise_for_status()
            if not response.content.startswith(b'%PDF-'):
                raise ValueError('Response is not a PDF')
            temporary = path.with_suffix('.download')
            temporary.write_bytes(response.content)
            with fitz.open(temporary, filetype='pdf') as doc:
                if not len(doc): raise ValueError('Empty PDF')
            temporary.replace(path)
            info = describe(path)
            if len(info['low_text_pages']) == info['pages']:
                target = DEST / '04_ocr_challenge' / 'documents' / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                path.replace(target)
                path = target
            return {'file': str(path.relative_to(DEST)).replace('\\', '/'),
                    'source_url': url, 'retrieved_at_utc': datetime.now(timezone.utc).isoformat(),
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                    'bytes': path.stat().st_size, 'type': 'downloaded_original', **info}
        except Exception:
            if attempt == 2: return {'file': str(path.relative_to(DEST)), 'source_url': url, 'status': 'download_failed'}
            time.sleep(1)

def main():
    DEST.mkdir(parents=True, exist_ok=True)
    tasks = [(group, filename) for group, files in GROUPS.items() for filename in files]
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(download, tasks))
    # A clearly labeled derivative tests OCR without changing any original PDF.
    source = DEST / '01_citigroup_starter/documents/2010-0407-Bushnell.pdf'
    if source.exists():
        folder = DEST / '04_ocr_challenge' / 'documents'; folder.mkdir(parents=True, exist_ok=True)
        output = folder / 'DERIVED_Bushnell_pages_1-2_image_only.pdf'
        with fitz.open(source) as original, fitz.open() as derived:
            for page in list(original)[:2]:
                image = page.get_pixmap(dpi=150).tobytes('png')
                new_page = derived.new_page(width=page.rect.width, height=page.rect.height)
                new_page.insert_image(new_page.rect, stream=image)
            derived.save(output)
        records.append({'file': str(output.relative_to(DEST)).replace('\\', '/'),
            'type': 'local_image_only_derivative',
            'source_file': '01_citigroup_starter/documents/2010-0407-Bushnell.pdf',
            'source_pages': [1, 2], 'transformation': 'Rendered at 150 DPI; image-only PDF, no text layer; original unchanged',
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'bytes': output.stat().st_size, **describe(output)})
    manifest = {'collection': 'Financial Crisis Inquiry Commission public records',
        'catalog_url': BASE, 'rights_source': BASE,
        'rights_note': 'The Stanford FCIC archive states that all FCIC records are in the public domain and may be used without restriction.',
        'purpose': 'Local witness identification, chronology, citation and question-preparation testing; not verified allegations or testimony truth.',
        'files': records}
    (DEST / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    success = [r for r in records if r.get('status') != 'download_failed']
    print(json.dumps({'folder': str(DEST), 'downloaded_originals': sum(r['type']=='downloaded_original' for r in success),
        'derived_ocr_files': sum(r['type']=='local_image_only_derivative' for r in success),
        'bytes': sum(r['bytes'] for r in success), 'pages': sum(r['pages'] for r in success),
        'failed': [r['file'] for r in records if r.get('status')=='download_failed']}, indent=2))

if __name__ == '__main__': main()
