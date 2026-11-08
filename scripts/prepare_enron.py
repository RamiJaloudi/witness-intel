"""Pin CMU's public Enron release and build an offline, header-grounded benchmark."""
import argparse
from collections import defaultdict
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
import hashlib
import heapq
import json
from pathlib import Path, PurePosixPath
import tarfile

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'datasets' / 'enron'
URL = 'https://www.cs.cmu.edu/~enron/enron_mail_20150507.tar.gz'
SOURCE_SHA256 = 'b3da1b3fe0369ec3140bb4fbce94702c33b7da810ec15d718b3fadf5cd748ca7'
ARCHIVE = DEST / 'source' / 'enron_mail_20150507.tar.gz'
CUSTODIANS = ('allen-p arnold-j beck-s buy-r dasovich-j delainey-d donohoe-t '
              'germany-c haedicke-m hayslett-r kaminski-v kean-s kitchen-l lay-k '
              'mann-k sanders-r shackleton-s skilling-j taylor-m whalley-g').split()
PER_CUSTODIAN = 10


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + '\n', encoding='utf-8')


def download():
    import httpx
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    if ARCHIVE.exists():
        print('Using existing archive; validation follows.', flush=True)
        return
    temporary = ARCHIVE.with_suffix('.partial')
    with httpx.stream('GET', URL, follow_redirects=True, timeout=120, trust_env=False) as response:
        response.raise_for_status()
        expected = int(response.headers.get('content-length', 0))
        total = 0
        next_report = 50_000_000
        with temporary.open('wb') as output:
            for chunk in response.iter_bytes(1024 * 1024):
                output.write(chunk)
                total += len(chunk)
                if total >= next_report:
                    print(f'Downloaded {total:,} bytes', flush=True)
                    next_report += 50_000_000
        if expected and total != expected:
            raise ValueError('Incomplete source download')
    temporary.replace(ARCHIVE)


def facts(raw):
    message = BytesParser(policy=policy.default).parsebytes(raw)
    body = message.get_body(preferencelist=('plain',))
    if not body or message.is_multipart() or any(message.iter_attachments()):
        raise ValueError('Benchmark requires a plain message without attachments')
    text = body.get_content()
    if not all(message.get(h) for h in ('Message-ID', 'From', 'To', 'Date', 'Subject')):
        raise ValueError('Required headers missing')
    # These facts describe the released record, not authorship, receipt or legal truth.
    quote = next((line.strip()[:160] for line in text.splitlines()
                  if 30 <= len(line.strip()) <= 200 and line.isascii()), None)
    if not quote:
        raise ValueError('No suitable literal body anchor')
    return {
        'message_id': str(message['Message-ID']),
        'from': str(message['From']), 'to': str(message['To']),
        'cc': str(message.get('Cc', '')), 'date': str(message['Date']),
        'subject': str(message['Subject']),
        'sender_addresses': [a.lower() for _, a in getaddresses([str(message['From'])])],
        'recipient_addresses': [a.lower() for _, a in getaddresses([str(message['To'])])],
        'body_quote': quote,
    }


def build():
    benchmark = DEST / 'benchmark_DO_NOT_IMPORT'
    documents = DEST / 'documents'
    if documents.exists() or (benchmark / 'answer_key.json').exists():
        raise ValueError('Benchmark already exists; validate it instead of overwriting evidence')
    source_hash = digest(ARCHIVE)
    if source_hash != SOURCE_SHA256:
        raise ValueError('Archive does not match the pinned CMU release checksum')
    pinned = DEST / 'source_manifest.json'
    if pinned.exists() and json.loads(pinned.read_text())['sha256'] != source_hash:
        raise ValueError('Source archive differs from the pinned release')
    # Take the ten lowest SHA-256 byte hashes per custodian. Stable across tar ordering.
    candidates = defaultdict(list)
    members = 0
    with tarfile.open(ARCHIVE, 'r|gz') as archive:
        for member in archive:
            archive.members.clear()  # Streaming scan does not need 500,000 cached TarInfo objects.
            if not member.isfile():
                continue
            members += 1
            if members % 100_000 == 0:
                print(f'Scanned {members:,} source files', flush=True)
            parts = PurePosixPath(member.name).parts
            if len(parts) < 4 or parts[0] != 'maildir' or parts[1] not in CUSTODIANS:
                continue
            if not 300 <= member.size <= 25_000:
                continue
            raw = archive.extractfile(member).read()
            sha = hashlib.sha256(raw).hexdigest()
            bucket = candidates[parts[1]]
            if any(item[2] == sha for item in bucket):
                continue
            if len(bucket) == PER_CUSTODIAN and -int(sha, 16) <= bucket[0][0]:
                continue
            try:
                metadata = facts(raw)
            except (ValueError, UnicodeError, LookupError):
                continue
            item = (-int(sha, 16), member.name, sha, raw, metadata)
            if len(bucket) < PER_CUSTODIAN:
                heapq.heappush(bucket, item)
            elif item[0] > bucket[0][0]:
                heapq.heapreplace(bucket, item)
    if any(len(candidates[c]) != PER_CUSTODIAN for c in CUSTODIANS):
        raise ValueError('Insufficient eligible messages for the fixed custodian selection')
    records, answers = [], []
    documents.mkdir(parents=True)
    for custodian in CUSTODIANS:
        for _, member, sha, raw, metadata in sorted(candidates[custodian], key=lambda row: row[2]):
            name = f'ENRON-{len(records)+1:04d}.eml'
            (documents / name).write_bytes(raw)
            records.append({'file': name, 'sha256': sha, 'bytes': len(raw),
                            'custodian_folder': custodian, 'archive_member': member})
            answers.append({'file': name, 'sha256': sha, **metadata})
    write_json(pinned, {'dataset': 'CMU Enron email corpus, May 7, 2015 release',
                       'source_url': URL, 'catalog_url': 'https://www.cs.cmu.edu/~enron/',
                       'archive': 'source/' + ARCHIVE.name, 'sha256': source_hash,
                       'bytes': ARCHIVE.stat().st_size, 'archive_regular_files': members,
                       'rights': 'Public research distribution by CMU; no blanket copyright license asserted.',
                       'attachments': 'Not included in this CMU release'})
    write_json(benchmark / 'manifest.json', {'version': 'ENRON-GOLD-1.0',
               'source_archive_sha256': source_hash,
               'selection': '10 lowest byte SHA-256 unique eligible messages per fixed custodian; 300..25000 bytes; complete headers, plain body and literal anchor.',
               'custodians': CUSTODIANS, 'files': records})
    write_json(benchmark / 'answer_key.json', {'version': 'ENRON-GOLD-1.0',
               'scope': 'Exact released-message headers, parsed addresses and literal body quotations only. Not verified identity, job roles, wrongdoing, authenticity, delivery or reading.',
               'adjudication': 'Deterministic source-derived reference; no human legal annotation or AI output.',
               'messages': answers})
    print(json.dumps(validate(), indent=2), flush=True)


def validate(root=DEST, check_archive=True):
    benchmark = root / 'benchmark_DO_NOT_IMPORT'
    manifest = json.loads((benchmark / 'manifest.json').read_text())
    key = json.loads((benchmark / 'answer_key.json').read_text())
    source = json.loads((root / 'source_manifest.json').read_text())
    if source['sha256'] != SOURCE_SHA256:
        raise ValueError('Source manifest differs from the pinned CMU release')
    if check_archive and digest(root / source['archive']) != source['sha256']:
        raise ValueError('Source checksum mismatch; run git lfs pull if this is a pointer')
    if manifest['source_archive_sha256'] != source['sha256']:
        raise ValueError('Manifest source mismatch')
    answers = {a['file']: a for a in key['messages']}
    expected = {r['file'] for r in manifest['files']}
    if len(expected) != 200 or len(answers) != 200 or set(answers) != expected:
        raise ValueError('Expected exactly 200 unique reference records')
    if {p.name for p in (root / 'documents').iterdir()} != expected:
        raise ValueError('Unexpected or missing evidence files')
    counts = defaultdict(int)
    from app.extract import extract
    for record in manifest['files']:
        path = root / 'documents' / record['file']
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record['sha256'] or len(raw) != record['bytes']:
            raise ValueError(f'Evidence checksum mismatch: {path.name}')
        answer = answers[path.name]
        if answer != {'file': path.name, 'sha256': record['sha256'], **facts(raw)}:
            raise ValueError(f'Reference mismatch: {path.name}')
        parts = list(extract(path))
        if len(parts) != 1 or answer['body_quote'] not in parts[0][1]:
            raise ValueError(f'Application extraction anchor missing: {path.name}')
        counts[record['custodian_folder']] += 1
    if dict(counts) != {c: PER_CUSTODIAN for c in CUSTODIANS}:
        raise ValueError('Custodian distribution mismatch')
    return {'documents_validated': 200, 'custodians': 20,
            'source_archive_verified': check_archive, 'all_hashes_verified': True,
            'source_facts_and_extraction_anchors_verified': True,
            'answer_key_excluded_from_evidence': True, 'ai_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--validate', action='store_true')
    args = parser.parse_args()
    if args.validate:
        print(json.dumps(validate(), indent=2))
    else:
        if args.download:
            download()
        build()


if __name__ == '__main__':
    main()
