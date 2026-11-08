import io
import json

from openpyxl import load_workbook

from app import providers
from app.inquire import Draft, DraftRow
from tests.test_workflow import client, create


def upload(client, mid, name, content, approve=True):
    assert client.post(f'/api/matters/{mid}/upload', files={'files': (name, content, 'text/plain')}).is_success
    workspace = client.get(f'/api/matters/{mid}/workspace').json()
    doc = next(item for item in workspace['documents'] if item['name'] == name)
    chunk = next(item for item in workspace['chunks'] if item['document_id'] == doc['id'])
    if approve:
        assert client.post(f"/api/matters/{mid}/chunks/{chunk['id']}/review", json={
            'approved': True, 'names_reviewed': True,
        }).is_success
    return doc, chunk


def test_selected_matter_citations_and_exports(client, monkeypatch):
    mid, other = create(client), create(client)
    doc, chunk = upload(client, mid, 'minutes.txt', b'The board approved the budget on June 1.')
    _, private = upload(client, mid, 'pending.txt', b'The board rejected the budget.', approve=False)
    foreign, _ = upload(client, other, 'elsewhere.txt', b'The board approved a different budget.')

    async def generate(provider, prompt, schema):
        assert provider == 'ollama' and schema is Draft
        body = json.loads(prompt.split('\n', 1)[1])
        assert [item['chunk_id'] for item in body['passages']] == [chunk['id']]
        assert 'alex@example.com' not in body['question']
        return Draft(rows=[
            DraftRow(label='June 1', text='=2+2', chunk_id=chunk['id'], quote='approved the budget'),
            DraftRow(label='Rejected', text='The board rejected the budget.', chunk_id=private['id'], quote='rejected the budget'),
            DraftRow(label='Wrong quote', text='The board expanded the budget.', chunk_id=chunk['id'], quote='expanded the budget'),
        ]), 'test-local'

    monkeypatch.setattr(providers, 'generate', generate)
    path = f'/api/matters/{mid}/inquire'
    assert client.post(path, json={'question': 'budget', 'document_ids': [foreign['id']]}).status_code == 400
    result = client.post(path, json={'question': 'board budget alex@example.com', 'document_ids': [doc['id']]})
    assert result.is_success, result.text
    body = result.json()
    assert body['passages_used'] == 1 and body['discarded_rows'] == 2
    assert body['rows'][0]['citation'].endswith('#passage-' + chunk['id'])
    assert client.get(body['rows'][0]['citation'].split('#')[0]).is_success
    export = {'question': body['question'], 'rows': body['rows']}
    csv = client.post(path + '/export', json={**export, 'format': 'csv'})
    assert csv.is_success and b"'=2+2" in csv.content
    xlsx = client.post(path + '/export', json={**export, 'format': 'xlsx'})
    assert xlsx.is_success
    sheet = load_workbook(io.BytesIO(xlsx.content)).active
    assert sheet['C2'].data_type == 's' and sheet['C2'].value == '=2+2'
    assert sheet['G2'].hyperlink.target.endswith('#passage-' + chunk['id'])
    assert client.post(f'/api/matters/{other}/inquire/export', json={**export, 'format': 'csv'}).status_code == 409


def test_privacy_gate_and_local_provider_error(client, monkeypatch):
    mid = create(client)
    _, chunk = upload(client, mid, 'minutes.txt', b'The board approved the budget.', approve=False)

    async def unreachable(*args):
        raise providers.ProviderError('Ollama is unavailable.')

    monkeypatch.setattr(providers, 'generate', unreachable)
    path = f'/api/matters/{mid}/inquire'
    assert client.post(path, json={'question': 'board budget'}).json()['passages_used'] == 0
    assert client.post(f"/api/matters/{mid}/chunks/{chunk['id']}/review", json={
        'approved': True, 'names_reviewed': True,
    }).is_success
    response = client.post(path, json={'question': 'board budget'})
    assert response.status_code == 502 and response.json()['detail'] == 'Ollama is unavailable.'
    monkeypatch.setenv('OLLAMA_URL', 'http://outside.invalid:11434')
    assert client.post(path, json={'question': 'board budget'}).status_code == 503
