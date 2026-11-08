import json
import shutil

import httpx
import pytest

from scripts import import_enron, prepare_enron


pytestmark = pytest.mark.skipif(
    not (prepare_enron.DEST / "benchmark_DO_NOT_IMPORT" / "manifest.json").is_file(),
    reason="Optional Enron benchmark is generated locally",
)


def test_frozen_benchmark_and_tamper_detection(tmp_path):
    root = tmp_path / 'enron'
    shutil.copytree(prepare_enron.DEST / 'documents', root / 'documents')
    shutil.copytree(prepare_enron.DEST / 'benchmark_DO_NOT_IMPORT', root / 'benchmark_DO_NOT_IMPORT')
    shutil.copy(prepare_enron.DEST / 'source_manifest.json', root / 'source_manifest.json')
    assert prepare_enron.validate(root, check_archive=False)['documents_validated'] == 200
    evidence = root / 'documents/ENRON-0001.eml'
    evidence.write_bytes(evidence.read_bytes() + b'\nChanged evidence\n')
    with pytest.raises(ValueError, match='checksum'):
        prepare_enron.validate(root, check_archive=False)


def test_reference_tampering_rejected(tmp_path):
    root = tmp_path / 'enron'
    shutil.copytree(prepare_enron.DEST / 'documents', root / 'documents')
    shutil.copytree(prepare_enron.DEST / 'benchmark_DO_NOT_IMPORT', root / 'benchmark_DO_NOT_IMPORT')
    shutil.copy(prepare_enron.DEST / 'source_manifest.json', root / 'source_manifest.json')
    path = root / 'benchmark_DO_NOT_IMPORT/answer_key.json'
    data = json.loads(path.read_text())
    data['messages'][0]['subject'] = 'Invented subject'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='Reference mismatch'):
        prepare_enron.validate(root, check_archive=False)


def test_import_only_evidence_and_safe_rerun(tmp_path):
    synthetic = tmp_path / 'synthetic.json'
    synthetic.write_text(json.dumps({'files': [{'sha256': 'synthetic'}]}))
    records = json.loads((prepare_enron.DEST / 'benchmark_DO_NOT_IMPORT/manifest.json').read_text())['files']
    docs = [{'id': 'original', 'sha256': 'synthetic', 'status': 'needs_privacy_review'}]
    by_name = {r['file']: r for r in records}
    posts = []

    def handle(request):
        if request.method == 'GET' and request.url.path == '/api/matters':
            data = [{'id': 'glenron', 'name': 'Glenron'}]
        elif request.method == 'GET':
            data = {'documents': list(docs), 'runs': []}
        else:
            payload = json.loads(request.content)
            posts.append(payload)
            record = by_name[payload['relative_path']]
            assert payload['path'] == str((prepare_enron.DEST / 'documents').resolve())
            assert payload['relative_path'].endswith('.eml')
            found = next((d for d in docs if d['sha256'] == record['sha256']), None)
            if not found:
                found = {'id': record['file'], 'sha256': record['sha256'], 'status': 'needs_privacy_review'}
                docs.append(found)
            data = found
        return httpx.Response(200, json=data)

    with httpx.Client(base_url='http://localhost/api', transport=httpx.MockTransport(handle)) as client:
        first = import_enron.import_benchmark(client, synthetic_manifest=synthetic)
        second = import_enron.import_benchmark(client, synthetic_manifest=synthetic)
    assert first['added_documents'] == 200
    assert second['added_documents'] == 0 and second['enron_documents'] == 200
    assert len(posts) == 400
    assert docs[0]['id'] == 'original'


@pytest.mark.parametrize('runs,docs,error', [
    ([{'status': 'running'}], [], 'active'),
    ([{'status': 'paused'}], [], 'active'),
    ([], [{'sha256': 'unrelated'}], 'unrelated'),
])
def test_import_refuses_active_or_foreign_corpus(tmp_path, runs, docs, error):
    synthetic = tmp_path / 'synthetic.json'
    synthetic.write_text(json.dumps({'files': [{'sha256': 'synthetic'}]}))

    def handle(request):
        assert request.method == 'GET'
        data = ([{'id': 'glenron', 'name': 'Glenron'}] if request.url.path == '/api/matters'
                else {'documents': docs, 'runs': runs})
        return httpx.Response(200, json=data)

    with httpx.Client(base_url='http://localhost/api', transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match=error):
            import_enron.import_benchmark(client, synthetic_manifest=synthetic)
