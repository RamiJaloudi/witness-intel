from tests.test_workflow import client, create, load

def test_empty_matter_reports_import_not_privacy(client):
    mid = create(client)
    result = client.post(f'/api/matters/{mid}/analyze', json={})
    assert result.status_code == 400 and 'No documents have been imported' in result.json()['detail']

def test_inventory_any_selected_folder_and_single_file_import(client, tmp_path, monkeypatch):
    extra = tmp_path / 'explicit-extra-root'; extra.mkdir()
    (extra / 'evidence.txt').write_text('Alex attended the meeting.')
    monkeypatch.delenv('IMPORT_ADDITIONAL_ROOTS', raising=False)
    mid = create(client)
    inventory = client.post(f'/api/matters/{mid}/folder/inventory', json={'path': str(extra)})
    assert inventory.json()['file_count'] == 1
    result = client.post(f'/api/matters/{mid}/folder/file', json={'path': str(extra), 'relative_path': 'evidence.txt'})
    assert result.status_code == 200 and result.json()['status'] == 'needs_privacy_review'
    duplicate = client.post(f'/api/matters/{mid}/folder/file', json={'path': str(extra), 'relative_path': 'evidence.txt'})
    assert duplicate.json()['duplicate_of'] == result.json()['id']
    assert client.post(f'/api/matters/{mid}/folder/file', json={'path': str(extra), 'relative_path': '../outside.txt'}).status_code == 400

def test_document_review_requires_names_and_current_passages(client):
    mid = create(client); workspace = load(client, mid)
    chunk = workspace['chunks'][0]; did = workspace['documents'][0]['id']
    url = f'/api/matters/{mid}/documents/{did}/approve-privacy'
    req = {'names_reviewed': False, 'chunk_ids': [chunk['id']], 'chunk_revisions': {chunk['id']: chunk['revision']}}
    assert client.post(url, json=req).status_code == 400
    req['names_reviewed'] = True; req['chunk_revisions'][chunk['id']] = 99
    assert client.post(url, json=req).status_code == 409
    req['chunk_revisions'][chunk['id']] = chunk['revision']
    other = create(client)
    assert client.post(f'/api/matters/{other}/documents/{did}/approve-privacy', json=req).status_code == 404
    assert client.post(url, json=req).json()['approved_passages'] == 1
    w = client.get(f'/api/matters/{mid}/workspace').json()
    assert all(c['approved'] for c in w['chunks'])
    assert any(a['action'] == 'document_privacy_review' for a in w['audit'])

def test_failed_files_remain_visible_and_require_explicit_scope_ack(client):
    mid = create(client)
    client.post(f'/api/matters/{mid}/upload', files=[('files', ('unsupported.pst', b'fixture', 'application/octet-stream'))])
    url = f'/api/matters/{mid}/exclude-failed'
    req = {'acknowledge_partial_corpus': False, 'reason': 'Synthetic failure scope test'}
    assert client.post(url, json=req).status_code == 400
    req['acknowledge_partial_corpus'] = True
    assert client.post(url, json=req).json()['excluded'] == 1
    w = client.get(f'/api/matters/{mid}/workspace').json()
    assert len(w['documents']) == 1 and w['documents'][0]['status'] == 'excluded'
    assert any(a['action'] == 'failed_documents_excluded' for a in w['audit'])
