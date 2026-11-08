import json
import httpx
import pytest
from app import main, providers
from app.evidence import Findings, Finding, SupportReview
from tests.test_workflow import client, create, load

def test_provider_errors_are_safe_and_actionable():
    error=providers.response_error('gemini',404)
    assert error.code=='MODEL_UNAVAILABLE' and 'Settings' in str(error)
    assert 'overloaded' in str(providers.response_error('gemini',503))

def test_transient_provider_retry_and_structured_output(monkeypatch):
    import asyncio
    real_client=httpx.AsyncClient;calls=[]
    def respond(request):
        calls.append(request)
        if len(calls)<3:return httpx.Response(503,json={'error':{'message':'PRIVATE RAW PROVIDER CONTENT'}})
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':'{"findings":[]}'}]}}]})
    monkeypatch.setattr(providers,'RETRY_DELAYS',(0,0))
    monkeypatch.setenv('GEMINI_API_KEY','synthetic-not-a-real-api-key')
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(respond),**kwargs))
    result,model=asyncio.run(providers.generate('gemini','synthetic test',Findings))
    assert len(calls)==3 and result.findings==[]

def test_verified_passage_survives_failure_and_resume_skips_it(client,monkeypatch):
    monkeypatch.setenv('PRIVACY_REVIEW_REQUIRED','false');monkeypatch.setenv('GEMINI_MODEL','fake-model')
    mid=create(client);load(client,mid)
    client.post(f'/api/matters/{mid}/upload',files=[('files',('second.txt',b'Nina reviewed the forecast.','text/plain'))])
    fail=True;seen=[]
    async def fake(provider,prompt,schema):
        body=json.loads(prompt)
        if schema is SupportReview:return SupportReview(supported_indices=[0]),'fake-model'
        seen.append(body['chunk_id'])
        if 'Nina' in body['text'] and fail:raise providers.ProviderError('Temporary model outage.','PROVIDER_UNAVAILABLE',503)
        person='Alex' if 'Alex' in body['text'] else 'Nina'
        quote='Alex attended the meeting.' if person=='Alex' else 'Nina reviewed the forecast.'
        return Findings(findings=[Finding(kind='witness',person=person,statement=quote,chunk_id=body['chunk_id'],quote=quote)]),'fake-model'
    monkeypatch.setattr(main,'generate',fake)
    result=client.post(f'/api/matters/{mid}/analyze',json={'provider':'gemini'})
    assert result.status_code==502 and 'Temporary model outage' in result.text
    w=client.get(f'/api/matters/{mid}/workspace').json()
    assert len(w['outputs'])==1 and w['runs'][0]['status']=='partial'
    first=seen[0];fail=False;seen.clear()
    result=client.post(f'/api/matters/{mid}/analyze',json={'provider':'gemini'}).json()
    assert result['processed_passages']==1 and result['complete']
    assert first not in seen
    assert len(client.get(f'/api/matters/{mid}/workspace').json()['outputs'])==2

def test_model_settings_and_status_are_explicit(client,monkeypatch,tmp_path):
    path=tmp_path/'.env';path.write_text('OTHER_SETTING=preserved\n');monkeypatch.setattr(main,'ENV_PATH',path)
    monkeypatch.setenv('GEMINI_MODEL','before-model')
    assert client.post('/api/settings/gemini-model',json={'model':'gemini-3.8-flash'}).status_code==200
    assert client.get('/api/settings').json()['gemini_model']=='gemini-3.8-flash'
    assert 'OTHER_SETTING=preserved' in path.read_text()
    mid=create(client)
    assert client.get(f'/api/matters/{mid}/analysis-status').json()['status']=='not_started'

def test_running_analysis_prevents_source_mutation(client):
    from sqlalchemy.orm import Session
    mid=create(client);workspace=load(client,mid);chunk=workspace['chunks'][0]
    with Session(main.engine) as s:
        s.add(main.Run(matter_id=mid,provider='ollama',purpose='extraction',status='running'));s.commit()
    assert client.post(f'/api/matters/{mid}/chunks/{chunk["id"]}/review',json={'approved':True,'names_reviewed':True,'text':'Altered source text'}).status_code==409
    assert client.post(f'/api/matters/{mid}/upload',files=[('files',('new.txt',b'New source','text/plain'))]).status_code==409


def test_failed_questions_identify_exact_witness_and_keep_safe_error(client,monkeypatch):
    from sqlalchemy.orm import Session
    monkeypatch.setenv('PRIVACY_REVIEW_REQUIRED','false')
    mid=create(client);w=load(client,mid)
    with Session(main.engine) as s:
        run=main.Run(matter_id=mid,provider='ollama',purpose='extraction',status='completed');s.add(run);s.flush()
        s.add(main.Output(matter_id=mid,run_id=run.id,kind='witness',payload={'person':'Alex','statement':'Alex attended the meeting.','quote':'Alex attended the meeting.','chunk_id':w['chunks'][0]['id']}));s.commit()
    async def unavailable(*args):
        raise providers.response_error('gemini',503)
    monkeypatch.setattr(main,'generate',unavailable)
    response=client.post(f'/api/matters/{mid}/questions',json={'witness':'Alex','provider':'gemini','question_count':50,'alignment':'unknown','proceeding':'deposition','examination':'direct','style':'open-ended'})
    assert response.status_code==502 and '503' in response.json()['detail']
    workspace=client.get(f'/api/matters/{mid}/workspace').json()
    run=next(r for r in workspace['runs'] if r['purpose']=='questions')
    assert run['status']=='failed'
    assert run['manifest'][0]=={'kind':'question_request','witness':'Alex','requested_count':50}
    assert not any(o['kind']=='questions' for o in workspace['outputs'])
    details=next(e for e in run['manifest'] if e.get('kind')=='question_error')
    assert details['code']=='PROVIDER_UNAVAILABLE' and details['http_status']==503
    progress=client.get(f'/api/matters/{mid}/question-progress').json()
    assert progress[0]['id']==run['id']
    other=create(client)
    assert client.get(f'/api/matters/{other}/question-progress').json()==[]


def test_duplicate_question_request_is_blocked_before_model_call(client,monkeypatch):
    from sqlalchemy.orm import Session
    mid=create(client)
    with Session(main.engine) as s:
        s.add(main.Run(matter_id=mid,provider='ollama',purpose='questions',status='running'));s.commit()
    async def forbidden(*args):raise AssertionError('Duplicate request reached provider')
    monkeypatch.setattr(main,'generate',forbidden)
    r=client.post(f'/api/matters/{mid}/questions',json={'witness':'Alex','provider':'ollama','alignment':'unknown','proceeding':'deposition','examination':'direct','style':'open-ended'})
    assert r.status_code==409 and 'already running' in r.json()['detail']
