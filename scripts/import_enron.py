"""Add only verified Enron benchmark evidence to Glenron; never import the answer key."""
import json
from pathlib import Path

import httpx

from scripts.prepare_enron import DEST, ROOT, validate

DATASET_NOTE = ('ENRON-GOLD-1.0 companion: ENRON-*.eml are real CMU Enron records, '
                'distinct from the original 500 fictional GLN-* Glenron records. '
                'Keep their people, events and assertions separate. Released message headers '
                'do not prove authorship, receipt, reading or misconduct. Benchmark answer keys '
                'are evaluation references and must never be imported as evidence.')


def annotate_workspace(mid):
    # The desktop launcher can override .env with its private local database URL.
    from scripts.launch import configuration
    configuration()
    from app import main as db
    from sqlalchemy.orm import Session
    with Session(db.engine) as session:
        matter = session.get(db.Matter, mid)
        if not matter or matter.name != 'Glenron':
            raise ValueError('Configured local database does not match the imported Glenron workspace')
        if DATASET_NOTE not in matter.instructions:
            matter.instructions = matter.instructions.rstrip() + '\n\n' + DATASET_NOTE
            matter.revision += 1
            db.audit(session, mid, 'benchmark_dataset_added', {
                'benchmark': 'ENRON-GOLD-1.0', 'documents': 200,
                'answer_key_imported': False, 'instructions_note_added': True})
            session.commit()


def import_benchmark(client, dataset=DEST, synthetic_manifest=None):
    validate(dataset, check_archive=False)
    manifest = json.loads((dataset / 'benchmark_DO_NOT_IMPORT/manifest.json').read_text())
    expected = {r['sha256'] for r in manifest['files']}
    synthetic_manifest = synthetic_manifest or ROOT / 'data/glenron/benchmark_DO_NOT_IMPORT/manifest.json'
    synthetic = {r['sha256'] for r in json.loads(Path(synthetic_manifest).read_text())['files']}

    def request(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    matches = [m for m in request('GET', '/matters') if m['name'] == 'Glenron']
    if len(matches) != 1:
        raise ValueError('Expected exactly one existing Glenron workspace; import the synthetic case first')
    mid = matches[0]['id']
    before = request('GET', f'/matters/{mid}/workspace?include_text=false')
    if any(d['sha256'] not in synthetic | expected for d in before['documents']):
        raise ValueError('Glenron contains unrelated evidence; no files were added')
    if any(r['status'] in ('running', 'paused') for r in before['runs']):
        raise ValueError('Finish the active Glenron analysis before adding benchmark evidence')
    before_ids = {d['id'] for d in before['documents']}
    for record in manifest['files']:
        result = request('POST', f'/matters/{mid}/folder/file', json={
            'path': str((dataset / 'documents').resolve()), 'relative_path': record['file']})
        if result.get('status') == 'failed':
            raise ValueError(f'Extraction failed: {record["file"]}; earlier imports are retained for retry')
    after = request('GET', f'/matters/{mid}/workspace?include_text=false')
    imported = [d for d in after['documents'] if d['sha256'] in expected]
    if {d['sha256'] for d in imported} != expected or any(d['status'] == 'failed' for d in imported):
        raise ValueError('Incomplete benchmark import; inspect the inventory')
    if not before_ids <= {d['id'] for d in after['documents']}:
        raise ValueError('Existing evidence changed unexpectedly')
    return {'workspace': 'Glenron', 'workspace_id': mid, 'benchmark': manifest['version'],
            'enron_documents': len(imported), 'total_documents': len(after['documents']),
            'added_documents': len(after['documents']) - len(before['documents']),
            'answer_key_imported': False, 'ai_generation_requested': False,
            'note': 'Automatic local indexing may process newly imported evidence.'}


def main():
    with httpx.Client(base_url='http://127.0.0.1:8765/api', timeout=120, trust_env=False) as client:
        report = import_benchmark(client)
    annotate_workspace(report['workspace_id'])
    report['dataset_instructions_note_saved'] = True
    target = ROOT / 'data/glenron/benchmark_DO_NOT_IMPORT/enron_import_report.json'
    target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
