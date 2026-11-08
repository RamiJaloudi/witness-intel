from app.question_context import AttorneyEdits,EditedQuestion
import json
import os
import uuid
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from app import main
from app.evidence import Findings, Finding, SupportReview, Questions, Question

@pytest.fixture
def client(tmp_path, monkeypatch):
    url = os.getenv('TEST_DATABASE_URL')
    base_engine = create_engine(url) if url else None
    schema = 'witness_test_' + uuid.uuid4().hex if url else None
    if schema:
        from sqlalchemy.schema import CreateSchema
        with base_engine.begin() as connection: connection.execute(CreateSchema(schema))
        db = base_engine.execution_options(schema_translate_map={None: schema})
    else:
        db = create_engine('sqlite:///' + str(tmp_path / 'test.db'), connect_args={'check_same_thread': False})
    main.Base.metadata.create_all(db)
    monkeypatch.setattr(main, 'engine', db)
    monkeypatch.setattr(main, 'ROOT', tmp_path / 'data')
    monkeypatch.setenv('IMPORT_ROOT', str(tmp_path / 'input'))
    monkeypatch.setenv('PRIVACY_KEY', 'long-enough-test-secret-for-privacy')
    monkeypatch.setenv('PRIVACY_REVIEW_REQUIRED', 'true')
    monkeypatch.setenv('INDEXING_MODE','manual')
    (tmp_path / 'input').mkdir()
    try:
        with TestClient(main.app) as c: yield c
    finally:
        if schema:
            from sqlalchemy.schema import DropSchema
            main.Base.metadata.drop_all(db)
            with base_engine.begin() as connection: connection.execute(DropSchema(schema))
            base_engine.dispose()
        else: db.dispose()

def create(client): return client.post('/api/matters', json={'name': 'Synthetic matter'}).json()['id']
def load(client, mid):
    result = client.post(f'/api/matters/{mid}/upload', files=[('files', ('source.txt', b'Alex attended the meeting. Contact alex@example.com.', 'text/plain'))])
    assert result.status_code == 200
    return client.get(f'/api/matters/{mid}/workspace').json()

def test_privacy_gate_and_matter_boundary(client, monkeypatch):
    mid, other = create(client), create(client)
    chunk = load(client, mid)['chunks'][0]
    assert 'alex@example.com' not in chunk['text']
    called = []
    async def forbidden(*args): called.append(True); raise AssertionError()
    monkeypatch.setattr(main, 'generate', forbidden)
    assert client.post(f'/api/matters/{mid}/analyze', json={}).status_code == 400
    assert not called
    assert client.post(f'/api/matters/{mid}/chunks/{chunk["id"]}/review', json={'approved': True}).status_code == 400
    assert client.post(f'/api/matters/{other}/chunks/{chunk["id"]}/review', json={'approved': True, 'names_reviewed': True}).status_code == 404
    assert client.post(f'/api/matters/{mid}/folder', json={'path': str(main.ROOT / 'nonexistent-evidence-folder')}).status_code == 400
    assert client.post('/api/matters', json={'name': 'evil'}, headers={'origin': 'https://example.com'}).status_code == 403

def test_verified_claims_questions_and_citation_freeze(client, monkeypatch):
    mid = create(client); chunk = load(client, mid)['chunks'][0]
    client.post(f'/api/matters/{mid}/chunks/{chunk["id"]}/review', json={'approved': True, 'names_reviewed': True})
    calls = []
    async def fake(provider, prompt, schema):
        if schema is AttorneyEdits:return AttorneyEdits(questions=[EditedQuestion(source_index=q['source_index'],question=q['question']) for q in json.loads(prompt)['questions']]),'fake-model'
        calls.append(json.loads(prompt))
        assert 'alex@example.com' not in prompt
        if schema is Findings:
            return Findings(findings=[Finding(kind='witness', person='Alex', statement='Alex attended the meeting.', chunk_id=chunk['id'], quote='Alex attended the meeting.'), Finding(kind='witness', person='Invented', statement='Invented attended.', chunk_id=chunk['id'], quote='Invented attended.')]), 'fake-model'
        if schema is SupportReview: return SupportReview(supported_indices=[0, 99]), 'fake-model'
        ids = [x['id'] for x in calls[-1]['claims']]
        return Questions(questions=[Question(question='You attended the meeting?', purpose='Attendance', claim_ids=ids), Question(question='Invented?', purpose='bad', claim_ids=['foreign-id'])]), 'fake-model'
    monkeypatch.setattr(main, 'generate', fake)
    result = client.post(f'/api/matters/{mid}/analyze', json={})
    assert result.json()['accepted'] == 1 and result.json()['rejected'] == 1
    workspace = client.get(f'/api/matters/{mid}/workspace').json()
    claim = workspace['outputs'][0]
    request = {'witness': 'Alex', 'alignment': 'adverse', 'proceeding': 'trial', 'examination': 'cross', 'style': 'leading'}
    assert client.post(f'/api/matters/{mid}/questions', json=request).status_code == 200
    client.post(f'/api/matters/{mid}/outputs/{claim["id"]}/review', json={'status': 'approved'})
    result = client.post(f'/api/matters/{mid}/questions', json=request)
    assert len(result.json()['questions']) == 1
    assert result.json()['actual_count'] == 1 and result.json()['requested_count'] == 100
    assert result.json()['warning']
    outline = next(o for o in client.get(f'/api/matters/{mid}/workspace').json()['outputs'] if o['kind'] == 'questions')
    review_url = f'/api/matters/{mid}/outlines/{outline["id"]}/questions/0/review'
    edited = {'question': 'Did you attend this meeting?', 'status': 'approved'}
    assert client.post(review_url, json=edited).status_code == 200
    updated = next(o for o in client.get(f'/api/matters/{mid}/workspace').json()['outputs'] if o['id'] == outline['id'])
    assert updated['payload']['questions'][0]['question'] == edited['question']
    assert updated['payload']['questions'][0]['original_question'] == 'You attended the meeting?'
    assert updated['payload']['questions'][0]['claim_ids'] == [claim['id']]
    other = create(client)
    assert client.post(f'/api/matters/{other}/outlines/{outline["id"]}/questions/0/review', json=edited).status_code == 404
    assert client.post(f'/api/matters/{mid}/chunks/{chunk["id"]}/review', json={'approved': True, 'names_reviewed': True, 'text': 'Changed evidence'}).status_code == 409

def test_failed_run_does_not_publish_partial_findings(client, monkeypatch):
    mid = create(client); chunk = load(client, mid)['chunks'][0]
    client.post(f'/api/matters/{mid}/chunks/{chunk["id"]}/review', json={'approved': True, 'names_reviewed': True})
    async def fail(*args): raise RuntimeError('SENSITIVE PROVIDER CONTENT')
    monkeypatch.setattr(main, 'generate', fail)
    response = client.post(f'/api/matters/{mid}/analyze', json={})
    assert response.status_code == 502 and 'SENSITIVE' not in response.text
    workspace = client.get(f'/api/matters/{mid}/workspace').json()
    assert not workspace['outputs'] and workspace['runs'][0]['status'] == 'failed'

def test_identifier_crossing_segment_boundary_is_masked(client):
    mid = create(client)
    text = 'x' * 5993 + ' alex@example.com end'
    client.post(f'/api/matters/{mid}/upload', files=[('files', ('boundary.txt', text.encode(), 'text/plain'))])
    chunks = client.get(f'/api/matters/{mid}/workspace').json()['chunks']
    assert 'alex@example.com' not in ''.join(c['text'] for c in chunks)

@pytest.mark.parametrize('target', [50, 200])
def test_question_targets_are_batched_distinct_and_cited(client, monkeypatch, target):
    mid = create(client); chunk = load(client, mid)['chunks'][0]
    client.post(f'/api/matters/{mid}/chunks/{chunk["id"]}/review',json={'approved':True,'names_reviewed':True})
    with Session(main.engine) as s:
        run = main.Run(matter_id=mid, provider='gemini', purpose='synthetic', status='completed')
        s.add(run); s.flush()
        claim = main.Output(matter_id=mid, run_id=run.id, kind='witness', review_status='approved', payload={'person': 'Alex', 'statement': 'Alex attended the meeting.', 'chunk_id': chunk['id'], 'quote': 'Alex attended the meeting.'})
        s.add(claim); s.commit(); claim_id = claim.id
    sizes = []
    async def fake(provider, prompt, schema):
        body = json.loads(prompt)
        if schema is AttorneyEdits:return AttorneyEdits(questions=[EditedQuestion(source_index=q['source_index'],question=q['question']) for q in body['questions']]),'fake-model'
        if schema is SupportReview:return SupportReview(supported_indices=list(range(len(body['candidates'])))), 'fake-model'
        sizes.append(body['batch']['requested_count'])
        assert all(c['id'] == claim_id for c in body['claims'])
        start = len(body['batch']['previous_questions'])
        questions = [Question(question=f'Question {start+i+1}: please explain aspect {start+i+1} of your attendance.', purpose='Synthetic fixture', claim_ids=[claim_id]) for i in range(body['batch']['requested_count'])]
        return Questions(questions=questions + [questions[0]]), 'fake-model'
    monkeypatch.setattr(main, 'generate', fake)
    req = {'witness': 'Alex', 'alignment': 'aligned', 'proceeding': 'deposition', 'examination': 'direct', 'style': 'open-ended', 'question_count': target}
    result = client.post(f'/api/matters/{mid}/questions', json=req)
    assert result.status_code == 200
    assert result.json()['actual_count'] == target and not result.json()['warning']
    assert max(sizes) <= 20 and sum(sizes) == target
    assert len({q['question'] for q in result.json()['questions']}) == target
    req['question_count'] = 201
    assert client.post(f'/api/matters/{mid}/questions', json=req).status_code == 422

def test_ollama_setting_requires_installed_model(client, monkeypatch, tmp_path):
    async def catalog(): return {'models':['qwen3.5:9b','qwen2.5:latest']}
    monkeypatch.setattr(main,'available_ollama_models',catalog)
    monkeypatch.setattr(main,'ENV_PATH',tmp_path/'settings.env')
    monkeypatch.setenv('OLLAMA_MODEL','qwen2.5:latest')
    assert client.post('/api/settings/ollama-model',json={'model':'missing:9b'}).status_code == 400
    assert main.configured_model('ollama') == 'qwen2.5:latest'
    response=client.post('/api/settings/ollama-model',json={'model':'qwen3.5:9b'})
    assert response.status_code == 200
    assert main.configured_model('ollama') == 'qwen3.5:9b'
    assert 'qwen3.5:9b' in (tmp_path/'settings.env').read_text()
    assert client.get('/api/settings').json()['default_provider'] == 'ollama'
    assert client.post('/api/settings/ollama-model',json={'model':'bad model\n'}).status_code == 422
