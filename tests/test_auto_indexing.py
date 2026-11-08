import asyncio
import os
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.test_workflow import client,create,load
from tests.test_workproduct import seed
from app import main,retrieval
from app.indexer import Indexer,indexing_mode
from app.providers import ProviderError


def enable_test_auto(monkeypatch):
    # Keep the TestClient lifespan worker idle; exercise a separate worker explicitly.
    async def idle():pass
    monkeypatch.setattr(main.app.state.indexer,'cycle',idle)
    monkeypatch.setenv('INDEXING_MODE','automatic')


def test_indexing_default_and_persisted_mode(client,monkeypatch,tmp_path):
    monkeypatch.delenv('INDEXING_MODE',raising=False)
    assert indexing_mode()=='automatic'
    async def idle():pass
    monkeypatch.setattr(main.app.state.indexer,'cycle',idle)
    monkeypatch.setattr(main,'ENV_PATH',tmp_path/'settings.env')
    assert client.get('/api/settings').json()['indexing_mode']=='automatic'
    r=client.post('/api/settings/indexing',json={'mode':'manual'})
    assert r.status_code==200 and indexing_mode()=='manual'
    assert 'INDEXING_MODE' in (tmp_path/'settings.env').read_text()
    assert client.post('/api/settings/indexing',json={'mode':'automatic'}).status_code==200
    assert indexing_mode()=='automatic'
    assert client.post('/api/settings/indexing',json={'mode':'invalid'}).status_code==422
    monkeypatch.setenv('INDEXING_MODE','manual')


def test_automatic_continues_batches_and_resumes_saved_vectors(client,monkeypatch):
    mid=create(client);chunk,cid=seed(client,mid)
    with Session(main.engine) as s:
        s.add_all([main.Chunk(document_id=chunk['document_id'],locator=f'part {i}',text=f'Alex reviewed ledger item {i}.',detections=[]) for i in range(119)]);s.commit()
    calls=[]
    async def embed(texts,query=False):calls.extend(texts);return [[1.0]+[0.0]*767 for t in texts]
    monkeypatch.setattr(retrieval,'embed',embed);enable_test_auto(monkeypatch)
    async def run():
        first=Indexer(main);await first.cycle()
        assert (await first.status(mid))['current']==50
        resumed=Indexer(main);await resumed.cycle();await resumed.cycle()
        status=await resumed.status(mid)
        assert status['phase']=='complete' and status['current']==120 and status['remaining']==0
    asyncio.run(run())
    assert len(calls)==120 and all('Alex' not in text for text in calls)
    with Session(main.engine) as s:
        rows=list(s.scalars(select(main.Embedding).where(main.Embedding.matter_id==mid)))
        assert len(rows)==120 and all(row.created_by=='local-indexer' for row in rows)
        audits=list(s.scalars(select(main.Audit).where(main.Audit.matter_id==mid,main.Audit.action=='local_embedding_batch')))
        assert len(audits)==3 and all(a.details['automatic'] for a in audits)


def test_manual_switch_stops_inflight_automatic_batch(client,monkeypatch):
    mid=create(client);chunk,cid=seed(client,mid)
    with Session(main.engine) as s:
        s.add(main.Chunk(document_id=chunk['document_id'],locator='part 2',text='Alex reviewed a second item.',detections=[]));s.commit()
    enable_test_auto(monkeypatch);calls=[]
    async def embed(texts,query=False):
        calls.extend(texts)
        if len(calls)==2:monkeypatch.setenv('INDEXING_MODE','manual')
        return [[1.0]+[0.0]*767]
    monkeypatch.setattr(retrieval,'embed',embed)
    async def run():
        worker=Indexer(main);await worker.cycle()
        status=await worker.status(mid)
        assert status['mode']=='manual' and status['current']==1 and status['remaining']==1
    asyncio.run(run())


def test_auto_skips_trash_and_pauses_for_local_analysis(client,monkeypatch):
    mid=create(client);chunk,cid=seed(client,mid)
    trash=create(client);seed(client,trash)
    client.post(f'/api/matters/{trash}/delete',json={'confirmation_name':'Synthetic matter'})
    with Session(main.engine) as s:
        s.add(main.Run(matter_id=mid,provider='ollama',purpose='synthetic',status='running'));s.commit()
    enable_test_auto(monkeypatch)
    async def forbidden(*args,**kwargs):raise AssertionError('Should not embed while paused or trashed')
    monkeypatch.setattr(retrieval,'embed',forbidden)
    async def run():
        worker=Indexer(main);assert trash not in worker.workspace_ids();await worker.cycle()
        assert (await worker.status(mid))['phase']=='paused_analysis'
    asyncio.run(run())


def test_provider_failure_retries_without_losing_completed_embeddings(client,monkeypatch):
    mid=create(client);chunk,cid=seed(client,mid);enable_test_auto(monkeypatch)
    async def unavailable(*args,**kwargs):raise ProviderError('Synthetic embedding unavailable')
    monkeypatch.setattr(retrieval,'embed',unavailable)
    async def run():
        worker=Indexer(main);await worker.cycle()
        status=await worker.status(mid)
        assert status['phase']=='error' and status['remaining']==1
        assert worker.next_check[mid]>0
        async def available(texts,query=False):return [[1.0]+[0.0]*767]
        monkeypatch.setattr(retrieval,'embed',available);worker.next_check[mid]=0
        await worker.cycle();assert (await worker.status(mid))['phase']=='complete'
    asyncio.run(run())


def test_trash_during_embedding_cannot_write_vectors(client,monkeypatch):
    mid=create(client);chunk,cid=seed(client,mid)
    async def embed(texts,query=False):
        await asyncio.to_thread(client.post,f'/api/matters/{mid}/delete',json={'confirmation_name':'Synthetic matter'})
        return [[1.0]+[0.0]*767]
    monkeypatch.setattr(retrieval,'embed',embed)
    result=client.post(f'/api/matters/{mid}/search/index',json={'limit':100})
    assert result.status_code==200
    with Session(main.engine) as s:assert not list(s.scalars(select(main.Embedding).where(main.Embedding.matter_id==mid)))
