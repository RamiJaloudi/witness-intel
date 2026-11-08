import asyncio
import json
from sqlalchemy import select
from sqlalchemy.orm import Session
import pytest
from app import main, job_runtime
from app.providers import ProviderError
from tests.test_workflow import client, create


def test_cancel_and_metadata_only_measurements(client,monkeypatch):
    mid = create(client)
    with Session(main.engine) as session:
        run = main.Run(matter_id=mid,provider='ollama',model='test',purpose='questions',status='running')
        session.add(run);session.commit();rid=run.id
    token=job_runtime.bind(main,rid,mid,{'witness':'Alex'})
    try:
        measurement=job_runtime.begin_measurement('ollama','test')
        job_runtime.request_attempt()
        job_runtime.capture_usage('ollama',{'prompt_eval_count':10,'eval_count':4,'eval_duration':2_000_000_000,'message':{'content':'DO_NOT_STORE_RESPONSE'}})
        job_runtime.finish_measurement(measurement)
        assert job_runtime.control(rid,'pause')
        assert job_runtime.live(rid)['pause_requested']
        assert job_runtime.control(rid,'resume')
        asyncio.run(job_runtime.boundary())
        assert job_runtime.control(rid,'cancel')
        with pytest.raises(ProviderError) as error:
            asyncio.run(job_runtime.boundary())
        assert error.value.code=='CANCELLED'
    finally:
        job_runtime.unbind(token)
    with Session(main.engine) as session:
        audit=session.scalar(select(main.Audit).where(main.Audit.action=='model_call_measured'))
        assert audit.details['input_tokens']==10
        assert audit.details['generation_seconds']==2
        assert 'DO_NOT_STORE_RESPONSE' not in json.dumps(audit.details)
    assert job_runtime.live(rid) is None
    def unavailable(*args,**kwargs):raise RuntimeError('Synthetic database failure')
    monkeypatch.setattr(main,'audit',unavailable)
    with pytest.raises(RuntimeError):job_runtime.bind(main,rid,mid,{'witness':'Alex'})
    assert job_runtime.live(rid) is None


def test_retry_uses_recorded_request_and_model(client,monkeypatch):
    mid=create(client);received=[]
    with Session(main.engine) as session:
        run=main.Run(matter_id=mid,provider='ollama',model='recorded-model',purpose='questions',status='interrupted')
        session.add(run);session.flush();rid=run.id
        main.audit(session,mid,'job_request_saved',{'run_id':rid,'request':{'provider':'ollama','witness':'Alex','alignment':'unknown','proceeding':'deposition','examination':'direct','style':'open-ended','question_count':50}});session.commit()
    async def fake(mid,request):
        from app.providers import active_model
        received.append((request.recovery_from,active_model.get()))
    monkeypatch.setattr(main,'questions',fake)
    assert client.post(f'/api/performance/jobs/{rid}/control',json={'action':'retry'}).status_code==200
    for _ in range(10):
        if received:break
        client.get(f'/api/matters/{mid}/attention')
    assert received==[(rid,'recorded-model')]


def test_question_batches_survive_cancel_and_recover_without_duplicate_wording(client,monkeypatch):
    from tests.test_workflow import load
    from app.evidence import Questions, Question, SupportReview
    from app.question_context import AttorneyEdits, EditedQuestion
    mid=create(client);chunk=load(client,mid)['chunks'][0]
    with Session(main.engine) as session:
        run=main.Run(matter_id=mid,provider='ollama',purpose='fixture',status='completed');session.add(run);session.flush()
        claim=main.Output(matter_id=mid,run_id=run.id,kind='witness',payload={'person':'Alex','statement':'Alex attended the meeting.','chunk_id':chunk['id'],'quote':'Alex attended the meeting.'})
        session.add(claim);session.commit();cid=claim.id
    async def fake(provider,prompt,schema):
        payload=json.loads(prompt)
        if schema is Questions:
            return Questions(questions=[Question(question='Did you attend the meeting?',purpose='Attendance',claim_ids=[cid])]),'test'
        if schema is AttorneyEdits:
            context=job_runtime.current_job.get()
            job_runtime.control(context['id'],'cancel')
            return AttorneyEdits(questions=[EditedQuestion(source_index=0,question='Did you attend the meeting?')]),'test'
        return SupportReview(supported_indices=[0]),'test'
    monkeypatch.setattr(main,'generate',fake)
    request={'provider':'ollama','witness':'Alex','alignment':'unknown','proceeding':'deposition','examination':'direct','style':'open-ended','question_count':50}
    response=client.post(f'/api/matters/{mid}/questions',json=request)
    assert response.status_code in {409,502}
    data=client.get(f'/api/matters/{mid}/workspace').json()
    stopped=next(run for run in data['runs'] if run['purpose']=='questions')
    assert stopped['status']=='cancelled'
    checkpoint=next(output for output in data['outputs'] if output['kind']=='questions_draft')
    assert checkpoint['payload']['actual_count']==1
    async def finish(provider,prompt,schema):
        if schema is Questions:return Questions(questions=[]),'test'
        return SupportReview(supported_indices=[]),'test'
    monkeypatch.setattr(main,'generate',finish)
    response=client.post(f'/api/matters/{mid}/questions',json={**request,'recovery_from':stopped['id']})
    assert response.status_code==200,response.text
    assert response.json()['actual_count']==1
    assert response.json()['questions'][0]['question']=='Did you attend the meeting?'
