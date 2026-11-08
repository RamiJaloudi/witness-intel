import json
import uuid
from pathlib import Path
import pytest
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from app import main,purge_storage
from tests.test_workflow import client,create,load

def trash(client,mid):
    assert client.post(f'/api/matters/{mid}/delete',json={'confirmation_name':'Synthetic matter'}).status_code==200

def preview(client,ids):
    response=client.post('/api/workspaces/trash/purge-preview',json={'workspace_ids':ids})
    assert response.status_code==200
    return response.json()

def purge(client,ids,confirmation):
    return client.post('/api/workspaces/trash/purge',json={'workspace_ids':ids,'confirmation':confirmation})

def test_individual_purge_removes_all_scoped_rows_and_copies(client,tmp_path):
    mid,other=create(client),create(client)
    source=tmp_path/'original-source';source.mkdir();original=source/'evidence.txt';original.write_text('Alex attended the meeting.')
    client.post(f'/api/matters/{mid}/folder/file',json={'path':str(source),'relative_path':'evidence.txt'})
    other_data=load(client,other)
    with Session(main.engine) as s:
        run=main.Run(matter_id=mid,provider='ollama',purpose='synthetic',status='completed');s.add(run);s.flush()
        s.add(main.Output(matter_id=mid,run_id=run.id,kind='witness',payload={'person':'Alex'}));s.commit()
    trash(client,mid);p=preview(client,[mid])
    assert p['counts']['documents']==1 and p['counts']['outputs']==1 and p['counts']['stored_files']==1
    assert purge(client,[mid],'wrong').status_code==400
    assert (main.ROOT/mid).exists()
    result=purge(client,[mid],p['confirmation']).json()
    assert result['completed'] and result['database_records_removed']
    assert not (main.ROOT/mid).exists() and original.read_text()=='Alex attended the meeting.'
    assert client.post(f'/api/matters/{mid}/restore').status_code==404
    with Session(main.engine) as s:
        assert s.get(main.Matter,mid) is None
        for model in [main.Document,main.Run,main.Output,main.Audit]:
            assert s.scalar(select(func.count()).select_from(model).where(model.matter_id==mid))==0
    assert client.get(f'/api/matters/{other}/workspace').status_code==200
    assert (main.ROOT/other/other_data['documents'][0]['id']/'original.txt').exists()
    receipt=json.loads((main.ROOT/'.purge-receipts'/(result['operation_id']+'.json')).read_text())
    assert receipt['state']=='complete' and 'name' not in receipt and 'text' not in receipt

def test_bulk_purge_validates_whole_selection_before_deleting(client):
    one,two,active=create(client),create(client),create(client)
    for mid in [one,two,active]:load(client,mid)
    trash(client,one);trash(client,two)
    response=purge(client,[one,active],'PURGE 2 WORKSPACES')
    assert response.status_code==409 and (main.ROOT/one).exists()
    assert purge(client,['../outside'],'anything').status_code==422
    p=preview(client,[one,two]);assert p['confirmation']=='PURGE 2 WORKSPACES'
    result=purge(client,[one,two],p['confirmation']).json()
    assert result['completed'] and result['purged_workspaces']==2
    assert not (main.ROOT/one).exists() and not (main.ROOT/two).exists()
    assert (main.ROOT/active).exists()

def test_file_cleanup_failure_is_visible_and_retryable(client,monkeypatch):
    mid=create(client);load(client,mid);trash(client,mid);p=preview(client,[mid])
    original=purge_storage.shutil.rmtree
    def locked(*args,**kwargs):raise PermissionError('synthetic file lock')
    monkeypatch.setattr(purge_storage.shutil,'rmtree',locked)
    result=purge(client,[mid],p['confirmation']).json()
    assert result['database_records_removed'] and not result['completed'] and result['pending_file_workspaces']==1
    operation=result['operation_id']
    assert any(op['operation_id']==operation for op in client.get('/api/workspaces/trash/pending-purges').json())
    assert (main.ROOT/'.purge-pending'/operation/mid).exists()
    monkeypatch.setattr(purge_storage.shutil,'rmtree',original)
    url=f'/api/workspaces/trash/pending-purges/{operation}/resolve'
    assert client.post(url,json={'confirmation':'wrong'}).status_code==400
    assert client.post(url,json={'confirmation':'RESOLVE PURGE'}).json()['completed']
    assert not (main.ROOT/'.purge-pending'/operation).exists()

def test_database_failure_restores_staged_storage(client,monkeypatch):
    mid=create(client);w=load(client,mid);trash(client,mid);p=preview(client,[mid])
    original=Session.execute
    def fail_on_delete(self,statement,*args,**kwargs):
        if getattr(statement,'is_delete',False) and statement.table.name=='documents':raise RuntimeError('synthetic database failure')
        return original(self,statement,*args,**kwargs)
    monkeypatch.setattr(Session,'execute',fail_on_delete)
    assert purge(client,[mid],p['confirmation']).status_code==500
    assert (main.ROOT/mid/w['documents'][0]['id']/'original.txt').exists()
    assert mid in {m['id'] for m in client.get('/api/workspaces/trash').json()}

def test_prepared_operation_can_be_recovered_without_deletion(client):
    mid=create(client);w=load(client,mid);trash(client,mid);operation=str(uuid.uuid4())
    purge_storage.prepare(main.ROOT,operation,[mid],{},main.now().isoformat())
    assert client.post(f'/api/matters/{mid}/restore').status_code==409
    result=client.post(f'/api/workspaces/trash/pending-purges/{operation}/resolve',json={'confirmation':'RESOLVE PURGE'}).json()
    assert result['recovered'] and (main.ROOT/mid/w['documents'][0]['id']/'original.txt').exists()
    assert client.post(f'/api/matters/{mid}/restore').status_code==200

def test_purge_rejects_linked_storage(client,tmp_path):
    mid=create(client);load(client,mid);outside=tmp_path/'outside-source';outside.mkdir();(outside/'keep.txt').write_text('preserve')
    link=main.ROOT/mid/'linked'
    try:link.symlink_to(outside,target_is_directory=True)
    except OSError:
        import os,shutil,subprocess
        shell=shutil.which('pwsh') or shutil.which('powershell')
        if os.name!='nt' or not shell:pytest.skip('Links/junctions unavailable for this test account')
        # Both creation targets are generated test paths inside the fixture directory.
        assert link.resolve().is_relative_to(tmp_path.resolve()) and outside.resolve().is_relative_to(tmp_path.resolve())
        quoted_link=str(link).replace("'","''");quoted_target=str(outside).replace("'","''")
        result=subprocess.run([shell,'-NoProfile','-NonInteractive','-Command',f"New-Item -ItemType Junction -Path '{quoted_link}' -Target '{quoted_target}' | Out-Null"],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:pytest.skip('Junction creation unavailable for this test account')
    trash(client,mid)
    assert client.post('/api/workspaces/trash/purge-preview',json={'workspace_ids':[mid]}).status_code==409
    assert (outside/'keep.txt').read_text()=='preserve'

def test_bulk_privacy_approval_is_scoped_confirmed_and_text_preserving(client):
    mid,other=create(client),create(client);first=load(client,mid);foreign=load(client,other)
    client.post(f'/api/matters/{mid}/upload',files=[('files',('second.txt',b'Nina reviewed the forecast.','text/plain'))])
    before=client.get(f'/api/matters/{mid}/workspace').json();ids=[d['id'] for d in before['documents']]
    url=f'/api/matters/{mid}/privacy/bulk-preview';p=client.post(url,json={'document_ids':ids}).json()
    assert p['document_count']==2 and p['pending_passages']==2
    req={'document_ids':ids,'names_reviewed':False,'expected_snapshots':p['snapshots']}
    assert client.post(f'/api/matters/{mid}/privacy/bulk-approve',json=req).status_code==400
    req['names_reviewed']=True
    assert client.post(f'/api/matters/{mid}/privacy/bulk-approve',json=req).json()['approved_documents']==2
    after=client.get(f'/api/matters/{mid}/workspace').json()
    assert all(c['approved'] for c in after['chunks'])
    assert {c['id']:c['text'] for c in before['chunks']}=={c['id']:c['text'] for c in after['chunks']}
    assert {c['id']:c['revision'] for c in before['chunks']}=={c['id']:c['revision'] for c in after['chunks']}
    assert all(d['status']=='privacy_approved' for d in after['documents'])
    assert not after['runs'] and any(a['action']=='bulk_document_privacy_review' for a in after['audit'])
    assert client.post(url,json={'document_ids':[foreign['documents'][0]['id']]}).status_code==404

def test_bulk_privacy_rejects_stale_snapshot_atomically(client):
    mid=create(client);w=load(client,mid);ids=[w['documents'][0]['id']];chunk=w['chunks'][0]
    p=client.post(f'/api/matters/{mid}/privacy/bulk-preview',json={'document_ids':ids}).json()
    client.post(f'/api/matters/{mid}/chunks/{chunk["id"]}/review',json={'approved':False,'text':'Alex provided an updated account.'})
    req={'document_ids':ids,'names_reviewed':True,'expected_snapshots':p['snapshots']}
    assert client.post(f'/api/matters/{mid}/privacy/bulk-approve',json=req).status_code==409
    assert not client.get(f'/api/matters/{mid}/workspace').json()['chunks'][0]['approved']

def test_bulk_approval_preserves_completed_checkpoints(client,monkeypatch):
    mid=create(client);w=load(client,mid);chunk=w['chunks'][0];ids=[w['documents'][0]['id']]
    monkeypatch.setenv('GEMINI_MODEL','synthetic-checkpoint-model')
    with Session(main.engine) as s:
        run=main.Run(matter_id=mid,provider='gemini',model='synthetic-checkpoint-model',purpose='extraction',status='completed',manifest=[{'id':chunk['id'],'revision':chunk['revision'],'processed':True}]);s.add(run);s.flush()
        s.add(main.Output(matter_id=mid,run_id=run.id,kind='witness',payload={'person':'Alex','chunk_id':chunk['id'],'quote':'Alex attended the meeting.'}));s.commit()
    p=client.post(f'/api/matters/{mid}/privacy/bulk-preview',json={'document_ids':ids}).json()
    req={'document_ids':ids,'names_reviewed':True,'expected_snapshots':p['snapshots']}
    assert client.post(f'/api/matters/{mid}/privacy/bulk-approve',json=req).status_code==200
    async def forbidden(*args):raise AssertionError('Completed checkpoint must not be reprocessed')
    monkeypatch.setattr(main,'generate',forbidden)
    result=client.post(f'/api/matters/{mid}/analyze',json={'provider':'gemini'}).json()
    assert result['complete'] and result['processed_passages']==0

def test_bulk_approval_is_blocked_during_running_analysis(client):
    mid=create(client);w=load(client,mid);ids=[w['documents'][0]['id']]
    p=client.post(f'/api/matters/{mid}/privacy/bulk-preview',json={'document_ids':ids}).json()
    with Session(main.engine) as s:s.add(main.Run(matter_id=mid,provider='ollama',purpose='extraction',status='running'));s.commit()
    assert client.post(f'/api/matters/{mid}/privacy/bulk-approve',json={'document_ids':ids,'names_reviewed':True,'expected_snapshots':p['snapshots']}).status_code==409
