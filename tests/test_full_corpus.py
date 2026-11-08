import asyncio
import hashlib
import json
import time
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.test_workflow import client,create
from app import main
from app.providers import configured_model,ProviderError
from app.evidence import Findings,Finding,SupportReview
from app.full_corpus import FullCorpusManager,source_scope,control_of


def sources(client,mid,count=5):
    client.post(f'/api/matters/{mid}/upload',files=[('files',('source.txt',b'Alex attended the meeting.','text/plain'))])
    chunk=client.get(f'/api/matters/{mid}/workspace').json()['chunks'][0]
    with Session(main.engine) as s:
        for i in range(count-1):s.add(main.Chunk(document_id=chunk['document_id'],locator=f'part {i+2}',text=f'Alex observed ledger item {i}.',detections=[]))
        s.commit()
    return chunk


def wait_for(client,mid,statuses,seconds=8):
    deadline=time.monotonic()+seconds;result=None
    while time.monotonic()<deadline:
        result=client.get(f'/api/matters/{mid}/full-analysis/status').json()
        if result.get('status') in statuses:return result
        time.sleep(.02)
    raise AssertionError(result)


def test_full_is_default_and_mode_is_persisted(client,monkeypatch,tmp_path):
    monkeypatch.delenv('ANALYSIS_MODE',raising=False);monkeypatch.setattr(main,'ENV_PATH',tmp_path/'settings.env')
    assert client.get('/api/settings').json()['analysis_mode']=='full'
    for mode in ['manual','full']:
        assert client.post('/api/settings/analysis-mode',json={'mode':mode}).status_code==200
        assert client.get('/api/settings').json()['analysis_mode']==mode
        assert mode in (tmp_path/'settings.env').read_text()


def test_full_scans_all_batches_and_drafts_only_after_extraction(client,monkeypatch):
    monkeypatch.setenv('PRIVACY_REVIEW_REQUIRED','false');mid=create(client);sources(client,mid,7);seen=[];drafted=[];model=configured_model('ollama')
    async def generate(provider,prompt,schema):
        body=json.loads(prompt)
        if schema is Findings:
            seen.append(body['chunk_id']);await asyncio.sleep(.001)
            monkeypatch.setenv('OLLAMA_MODEL','changed-during-run')
            return Findings(findings=[Finding(kind='witness',person='Alex',statement=body['text'],quote=body['text'],chunk_id=body['chunk_id'])]),configured_model(provider)
        return SupportReview(supported_indices=[0]),configured_model(provider)
    async def draft(mid,request):
        with Session(main.engine) as s:assert all(x['processed'] for x in source_scope(s,mid,'ollama',model))
        assert request.provider=='ollama' and request.question_count==50
        drafted.append(request.witness);return {'run_id':'fixture-work-product'}
    monkeypatch.setattr(main,'generate',generate);monkeypatch.setattr(main.app.state,'work_product_draft',draft)
    result=client.post(f'/api/matters/{mid}/full-analysis/start',json={'batch_size':2,'question_settings':{'Alex':{'questionCount':'50'}}})
    assert result.status_code==200,result.text
    status=wait_for(client,mid,{'completed','failed'});assert status['status']=='completed',status
    assert status['processed_passages']==7 and status['completed_documents']==1 and status['witnesses_completed']==1
    assert len(seen)==len(set(seen))==7 and drafted==['Alex'] and status['model']==model
    with Session(main.engine) as s:
        children=list(s.scalars(select(main.Run).where(main.Run.matter_id==mid,main.Run.purpose=='extraction')))
        assert len(children)==4 and all(r.model==model for r in children)


def test_pause_resume_preserves_checkpoints_and_scope_lock(client,monkeypatch):
    monkeypatch.setenv('PRIVACY_REVIEW_REQUIRED','false');mid=create(client);sources(client,mid,5);seen=[]
    async def generate(provider,prompt,schema):
        seen.append(json.loads(prompt)['chunk_id']);await asyncio.sleep(.05);return Findings(findings=[]),configured_model(provider)
    monkeypatch.setattr(main,'generate',generate)
    assert client.post(f'/api/matters/{mid}/full-analysis/start',json={'batch_size':5,'draft_work_product':False}).status_code==200
    assert client.post(f'/api/matters/{mid}/full-analysis/control',json={'action':'pause'}).status_code==200
    paused=wait_for(client,mid,{'paused'});assert paused['processed_passages']<5
    assert client.post(f'/api/matters/{mid}/upload',files=[('files',('extra.txt',b'Extra evidence','text/plain'))]).status_code==409
    assert client.post(f'/api/matters/{mid}/full-analysis/start',json={}).status_code==409
    assert client.post(f'/api/matters/{mid}/full-analysis/control',json={'action':'resume'}).status_code==200
    complete=wait_for(client,mid,{'completed','failed'});assert complete['status']=='completed',complete
    assert len(seen)==len(set(seen))==5


def test_provider_failure_never_claims_full_coverage_and_resume_skips_success(client,monkeypatch):
    monkeypatch.setenv('PRIVACY_REVIEW_REQUIRED','false');mid=create(client);sources(client,mid,3);seen=[];failing=[True]
    async def generate(provider,prompt,schema):
        cid=json.loads(prompt)['chunk_id'];seen.append(cid)
        if len(seen)==2 and failing[0]:raise ProviderError('Invalid structured output')
        return Findings(findings=[]),configured_model(provider)
    monkeypatch.setattr(main,'generate',generate)
    client.post(f'/api/matters/{mid}/full-analysis/start',json={'draft_work_product':False})
    failed=wait_for(client,mid,{'failed'});assert failed['processed_passages']==1
    first=seen[0];failing[0]=False
    assert client.post(f'/api/matters/{mid}/full-analysis/control',json={'action':'resume'}).status_code==200
    done=wait_for(client,mid,{'completed','failed'});assert done['status']=='completed' and done['processed_passages']==3
    assert seen.count(first)==1


def test_transient_retry_is_visible_and_can_be_paused(client,monkeypatch):
    monkeypatch.setenv('PRIVACY_REVIEW_REQUIRED','false');mid=create(client);sources(client,mid,1)
    async def unavailable(*args):raise ProviderError('Gemini is temporarily unavailable or overloaded')
    monkeypatch.setattr(main,'generate',unavailable)
    client.post(f'/api/matters/{mid}/full-analysis/start',json={'draft_work_product':False})
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        status=client.get(f'/api/matters/{mid}/full-analysis/status').json()
        if status.get('phase')=='retry_wait':break
        time.sleep(.02)
    assert status['retry_count']==1 and status['next_retry_at'] and status['processed_passages']==0
    client.post(f'/api/matters/{mid}/full-analysis/control',json={'action':'pause'})
    assert wait_for(client,mid,{'paused'})['status']=='paused'


def test_restart_recovery_retains_completed_child_passages(client,monkeypatch):
    monkeypatch.setenv('PRIVACY_REVIEW_REQUIRED','false');mid=create(client);sources(client,mid,3);model=configured_model('gemini');seen=[]
    with Session(main.engine) as s:
        scope=source_scope(s,mid,'gemini',model)
        parent=main.Run(matter_id=mid,provider='gemini',model=model,purpose='full_corpus',manifest=[{'kind':'control','batch_size':2,'draft_work_product':False},*scope]);s.add(parent);s.flush();rid=parent.id
        child=main.Run(matter_id=mid,provider='gemini',model=model,purpose='extraction',manifest=[{**scope[0],'processed':True,'full_run_id':rid}]);s.add(child);s.commit();child_id=child.id;first=scope[0]['id']
    async def generate(provider,prompt,schema):seen.append(json.loads(prompt)['chunk_id']);return Findings(findings=[]),configured_model(provider)
    monkeypatch.setattr(main,'generate',generate)
    async def recover():
        manager=FullCorpusManager(main);await manager.start();await asyncio.gather(*manager.tasks.values())
    asyncio.run(recover())
    with Session(main.engine) as s:
        assert s.get(main.Run,rid).status=='completed' and s.get(main.Run,child_id).status=='interrupted'
    assert first not in seen and len(seen)==2


def test_start_rejects_failed_documents_and_invalid_settings(client,monkeypatch):
    monkeypatch.setenv('PRIVACY_REVIEW_REQUIRED','false');mid=create(client);chunk=sources(client,mid,1)
    assert client.post(f'/api/matters/{mid}/full-analysis/start',json={'question_settings':{'Alex':{'questionCount':'bad'}}}).status_code==400
    with Session(main.engine) as s:s.get(main.Document,chunk['document_id']).status='failed';s.commit()
    assert client.post(f'/api/matters/{mid}/full-analysis/start',json={}).status_code==400

def test_cancel_preserves_progress_and_releases_workspace(client,monkeypatch):
    monkeypatch.setenv('PRIVACY_REVIEW_REQUIRED','false');mid=create(client);sources(client,mid,5)
    async def generate(provider,prompt,schema):await asyncio.sleep(.05);return Findings(findings=[]),configured_model(provider)
    monkeypatch.setattr(main,'generate',generate)
    client.post(f'/api/matters/{mid}/full-analysis/start',json={'draft_work_product':False})
    client.post(f'/api/matters/{mid}/full-analysis/control',json={'action':'cancel'})
    state=wait_for(client,mid,{'cancelled'});assert state['processed_passages']<5
    assert client.post(f'/api/matters/{mid}/upload',files=[('files',('new.txt',b'Alex wrote new material.','text/plain'))]).status_code==200


def test_full_coverage_does_not_include_other_workspaces(client,monkeypatch):
    monkeypatch.setenv('PRIVACY_REVIEW_REQUIRED','false');mid=create(client);sources(client,mid,2);other=create(client);sources(client,other,4)
    async def generate(provider,prompt,schema):return Findings(findings=[]),configured_model(provider)
    monkeypatch.setattr(main,'generate',generate)
    client.post(f'/api/matters/{mid}/full-analysis/start',json={'draft_work_product':False})
    state=wait_for(client,mid,{'completed'});assert state['total_passages']==2
    assert client.get(f'/api/matters/{other}/full-analysis/status').json()['status']=='not_started'


def test_hash_mismatch_cannot_be_reported_as_completed(client,monkeypatch):
    monkeypatch.setenv('PRIVACY_REVIEW_REQUIRED','false');mid=create(client);chunk=sources(client,mid,1)
    with Session(main.engine) as s:
        c=s.get(main.Chunk,chunk['id']);s.add(main.Run(matter_id=mid,provider='gemini',model=configured_model('gemini'),purpose='extraction',status='completed',manifest=[{'id':c.id,'revision':c.revision,'hash':'incorrect-source-hash','processed':True}]))
        s.commit()
    result=client.post(f'/api/matters/{mid}/full-analysis/start',json={'provider':'gemini','draft_work_product':False})
    assert result.status_code==409 and 'Source text changed' in result.json()['detail']
