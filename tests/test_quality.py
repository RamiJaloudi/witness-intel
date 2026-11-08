import hashlib
import json
from app import main, quality
from app.quality import HeaderPrediction,score_prediction
from tests.test_workflow import client,create


def test_public_benchmark_never_passes_key_to_model(client,monkeypatch,tmp_path):
    mid=create(client);calls=[]
    docs = tmp_path / 'documents'
    reference = tmp_path / 'benchmark_DO_NOT_IMPORT'
    docs.mkdir()
    reference.mkdir()
    source = docs / 'fictional.eml'
    source.write_bytes(
        b'Message-ID: <example@example.test>\nFrom: sender@example.test\n'
        b'To: recipient@example.test\nSubject: Fictional review\n\n'
        b'The reconciliation is due Friday.\n'
    )
    expected = {
        'file': source.name, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'message_id': '<example@example.test>', 'sender_addresses': ['sender@example.test'],
        'recipient_addresses': ['recipient@example.test'], 'subject': 'Fictional review',
        'body_quote': 'The reconciliation is due Friday.',
    }
    key = {'messages': [expected], 'fixture_marker': 'PRIVATE_REFERENCE_MARKER'}
    (reference / 'answer_key.json').write_text(json.dumps(key), encoding='utf-8')
    monkeypatch.setattr(quality, 'DATASET', tmp_path)
    async def fake(provider,prompt,schema):
        payload=json.loads(prompt);assert set(payload)=={'task','source'}
        assert 'expected' not in payload and 'answer_key' not in payload
        assert key['fixture_marker'] not in prompt
        calls.append(provider)
        return HeaderPrediction(**{field:expected[field] for field in HeaderPrediction.model_fields}),'test'
    monkeypatch.setattr(main,'generate',fake)
    result=client.post(f'/api/matters/{mid}/benchmarks',json={'provider':'ollama','sample_size':1})
    assert result.status_code==200
    reports=[]
    for _ in range(20):
        reports=client.get(f'/api/matters/{mid}/benchmarks').json()['reports']
        if reports and reports[0]['status']=='completed':break
    assert calls==['ollama']
    assert reports[0]['scores']['quote_grounded']==1
    assert reports[0]['scores']['sender_correct']==1


def test_score_records_metadata_misses_not_witness_roles():
    expected={'message_id':'id','sender_addresses':['a@example.test'],'recipient_addresses':['b@example.test'],'subject':'subject','body_quote':'Exact body quotation'}
    prediction=HeaderPrediction(message_id='id',sender_addresses=[],recipient_addresses=['invented@example.test'],subject='subject',body_quote='not in source')
    score=score_prediction(prediction,expected,'Exact body quotation')
    assert score['quote_grounded'] is False
    assert score['extra_addresses']==['invented@example.test']
    assert score['missing_addresses']==['a@example.test','b@example.test']


def test_attorney_reference_enables_partial_name_recall_without_false_positive_claims(client):
    from tests.test_workspace_tools import findings
    mid, ids = findings(client)
    request = {'names':['Alex','Blair'],'scope_note':'Attorney reviewed this synthetic reference.','complete':False}
    assert client.post(f'/api/matters/{mid}/witness-reference',json=request).status_code==200
    audit=client.get(f'/api/matters/{mid}/quality-audit').json()
    row=audit['candidate_coverage'][0]
    assert row['recall']==.5 and row['precision'] is None
    assert row['missing']==['Blair']
    assert row['unlisted_candidates']==['A. Example']
    assert audit['witness_reference']['complete'] is False
