import copy
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.test_workflow import client,create
from app import main


def seed(client,mid):
    client.post(f'/api/matters/{mid}/upload',files=[('files',('evidence.txt',b'Alex attended the meeting. Jordan reviewed the notes.','text/plain'))])
    chunk=client.get(f'/api/matters/{mid}/workspace').json()['chunks'][0]
    with Session(main.engine) as s:
        run=main.Run(matter_id=mid,provider='ollama',purpose='fixture',status='completed');s.add(run);s.flush();claims=[]
        for person in ['Alex','Alex','Jordan']:
            text='Alex attended the meeting.' if person=='Alex' else 'Jordan reviewed the notes.'
            claim=main.Output(matter_id=mid,run_id=run.id,kind='witness',payload={'person':person,'statement':text,'quote':text,'chunk_id':chunk['id']});s.add(claim);claims.append(claim)
        s.flush();outlines=[]
        for person,claim in [('Alex',claims[0]),('Jordan',claims[2])]:
            outline=main.Output(matter_id=mid,run_id=run.id,kind='questions',payload={'settings':{'witness':person},'questions':[{'question':'What did you observe?','purpose':'Knowledge','claim_ids':[claim.id],'review_status':'needs_review'},{'question':'Protected approved wording?','purpose':'Protected','claim_ids':[claim.id],'review_status':'approved'},{'question':'Protected rejected wording?','purpose':'Protected','claim_ids':[claim.id],'review_status':'rejected'}]});s.add(outline);outlines.append(outline)
        s.commit();return [c.id for c in claims],[o.id for o in outlines],chunk


def preview(client,mid,request):
    result=client.post(f'/api/matters/{mid}/review/bulk-preview',json=request)
    assert result.status_code==200,result.text
    return result.json()


def test_bulk_witness_approval_and_audit_preserve_wording(client):
    mid=create(client);ids,outlines,chunk=seed(client,mid)
    request={'scope':'witness','witness':'Alex','include':'evidence_and_questions','status':'approved'}
    p=preview(client,mid,request);assert p['evidence_count']==2 and p['question_count']==1
    assert client.post(f'/api/matters/{mid}/review/bulk',json={**request,'snapshot':p['snapshot']}).status_code==400
    result=client.post(f'/api/matters/{mid}/review/bulk',json={**request,'snapshot':p['snapshot'],'reviewed':True})
    assert result.status_code==200 and result.json()['updated_questions']==1
    with Session(main.engine) as s:
        assert s.get(main.Output,ids[0]).review_status=='approved' and s.get(main.Output,ids[2]).review_status=='needs_review'
        qs=s.get(main.Output,outlines[0]).payload['questions']
        assert qs[0]['question']=='What did you observe?' and qs[0]['review_status']=='approved'
        assert qs[1]['question']=='Protected approved wording?' and qs[2]['review_status']=='rejected'
        assert s.get(main.Output,outlines[1]).payload['questions'][0]['review_status']=='needs_review'
        audit=s.scalar(select(main.Audit).where(main.Audit.matter_id==mid,main.Audit.action=='bulk_work_product_review'))
        assert len(audit.details['before_items'])==3 and not audit.details['wording_modified'] and not audit.details['privacy_approval_modified']
        assert not s.get(main.Chunk,chunk['id']).approved


def test_workspace_approve_all_pending(client):
    mid=create(client);ids,outlines,chunk=seed(client,mid)
    request={'scope':'workspace','include':'evidence_and_questions','status':'approved'}
    p=preview(client,mid,request);assert p['total']==5
    result=client.post(f'/api/matters/{mid}/review/bulk',json={**request,'snapshot':p['snapshot'],'reviewed':True})
    assert result.status_code==200 and result.json()['updated_evidence']==3 and result.json()['updated_questions']==2


def test_bulk_scope_and_snapshot_reject_stale_changes_atomically(client):
    mid=create(client);ids,outlines,chunk=seed(client,mid);other=create(client);foreign,_,_=seed(client,other)
    assert client.post(f'/api/matters/{mid}/review/bulk-preview',json={'scope':'selected','output_ids':[ids[0],foreign[0]],'status':'approved'}).status_code==400
    request={'scope':'selected','output_ids':ids[:2],'status':'approved'};p=preview(client,mid,request)
    client.post(f'/api/matters/{mid}/outputs/{ids[0]}/review',json={'status':'approved'})
    result=client.post(f'/api/matters/{mid}/review/bulk',json={**request,'snapshot':p['snapshot'],'reviewed':True})
    assert result.status_code==409
    with Session(main.engine) as s:assert s.get(main.Output,ids[1]).review_status=='needs_review'


def test_bulk_selected_reject_and_reset(client):
    mid=create(client);ids,outlines,chunk=seed(client,mid)
    for status in ['rejected','needs_review']:
        request={'scope':'selected','output_ids':ids[:2],'status':status};p=preview(client,mid,request)
        assert client.post(f'/api/matters/{mid}/review/bulk',json={**request,'snapshot':p['snapshot'],'reviewed':True}).status_code==200
        with Session(main.engine) as s:assert all(s.get(main.Output,cid).review_status==status for cid in ids[:2])


def test_bulk_approval_requires_available_source_support(client):
    mid=create(client);ids,outlines,chunk=seed(client,mid)
    with Session(main.engine) as s:s.get(main.Document,chunk['document_id']).status='excluded';s.commit()
    request={'scope':'workspace','include':'evidence_and_questions','status':'approved'}
    assert client.post(f'/api/matters/{mid}/review/bulk-preview',json=request).status_code==409
    with Session(main.engine) as s:assert all(s.get(main.Output,cid).review_status=='needs_review' for cid in ids)


def test_bulk_question_snapshot_detects_wording_edits(client):
    mid=create(client);ids,outlines,chunk=seed(client,mid)
    request={'scope':'workspace','include':'questions','status':'approved'};p=preview(client,mid,request)
    client.post(f'/api/matters/{mid}/outlines/{outlines[0]}/questions/0/review',json={'question':'New attorney wording?','status':'needs_review'})
    result=client.post(f'/api/matters/{mid}/review/bulk',json={**request,'snapshot':p['snapshot'],'reviewed':True})
    assert result.status_code==409
    with Session(main.engine) as s:assert s.get(main.Output,outlines[1]).payload['questions'][0]['review_status']=='needs_review'
