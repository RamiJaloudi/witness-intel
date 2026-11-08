"""Snapshot-checked bulk review of source-linked evidence and draft questions."""
import hashlib
import json
from typing import Literal
from uuid import UUID
from fastapi import HTTPException
from pydantic import BaseModel,Field
from sqlalchemy import select
from sqlalchemy.orm import Session

class BulkReviewInput(BaseModel):
    scope:Literal['selected','witness','workspace']='witness'
    witness:str=Field(default='',max_length=200)
    include:Literal['evidence','questions','evidence_and_questions']='evidence'
    output_ids:list[UUID]=Field(default_factory=list,max_length=5000)
    status:Literal['approved','rejected','needs_review']='approved'
class BulkReviewApply(BulkReviewInput):
    snapshot:str=Field(min_length=64,max_length=64)
    reviewed:bool=False


def preview_review(s,mid,req,lock=False):
    from . import main as db
    matter=db.matter(s,mid)
    if req.scope=='witness' and not req.witness.strip():raise HTTPException(400,'Select a witness first')
    ids={str(value) for value in req.output_ids}
    if req.scope=='selected' and not ids:raise HTTPException(400,'Select evidence to review')
    if req.scope=='selected' and req.include!='evidence':raise HTTPException(400,'Selected-row review applies to evidence only')
    query=select(db.Output).where(db.Output.matter_id==mid).order_by(db.Output.id)
    if lock:query=query.with_for_update()
    rows=list(s.scalars(query));by_id={o.id:o for o in rows}
    if ids-set(by_id):raise HTTPException(400,'Selection includes missing or out-of-workspace evidence')
    kinds={'witness','event','relationship','document_context'}
    if ids and any(by_id[oid].kind not in kinds for oid in ids):raise HTTPException(400,'Select source-linked evidence findings only')
    witness_questions=[o for o in rows if o.kind=='questions' and o.payload.get('settings',{}).get('witness')==req.witness]
    context_ids={cid for o in witness_questions for q in o.payload.get('questions',[]) for cid in q.get('claim_ids',[])}
    targets=[];evidence=[];questions=[]
    for output in rows:
        if req.scope=='selected' and output.id not in ids:continue
        if output.kind in kinds and req.include in {'evidence','evidence_and_questions'}:
            if req.scope=='witness' and req.witness not in [output.payload.get('person'),output.payload.get('other_person')] and output.id not in context_ids:continue
            if req.scope!='selected' and output.review_status!='needs_review':continue
            if output.review_status==req.status:continue
            evidence.append(output);targets.append({'output_id':output.id,'revision':output.revision,'before':output.review_status,'kind':output.kind,'payload_hash':hashlib.sha256(json.dumps(output.payload,sort_keys=True).encode()).hexdigest()})
        if output.kind=='questions' and req.include in {'questions','evidence_and_questions'}:
            if req.scope=='witness' and output.payload.get('settings',{}).get('witness')!=req.witness:continue
            for index,question in enumerate(output.payload.get('questions',[])):
                before=question.get('review_status','needs_review')
                if before!='needs_review' or before==req.status:continue
                questions.append((output,index,question));targets.append({'output_id':output.id,'revision':output.revision,'question_index':index,'before':before,'kind':'question','payload_hash':hashlib.sha256(json.dumps(question,sort_keys=True).encode()).hexdigest()})
    if not targets:raise HTTPException(400,'No matching items need this review change')
    if len(targets)>10000:raise HTTPException(400,'Select a smaller witness or evidence batch (maximum 10,000 review items)')
    def source_valid(output):
        if not output or output.matter_id!=mid or output.kind not in kinds:return False
        chunk=s.get(db.Chunk,output.payload.get('chunk_id'));doc=s.get(db.Document,chunk.document_id) if chunk else None
        return bool(chunk and doc and doc.matter_id==mid and doc.status not in {'excluded','failed'} and output.payload.get('quote') and output.payload['quote'] in chunk.text)
    if req.status=='approved':
        if any(not source_valid(o) for o in evidence):raise HTTPException(409,'Some selected evidence has missing, excluded or invalid source support. Inspect its citations before approval.')
        approved_ids={o.id for o in evidence}
        for outline,index,q in questions:
            refs=q.get('claim_ids',[])
            if q.get('exploratory'):
                valid=not refs
            else:
                valid=bool(refs) and all(source_valid(by_id.get(cid)) and (by_id[cid].review_status!='rejected' or cid in approved_ids) for cid in refs)
            if not valid:raise HTTPException(409,'Some draft questions have missing or rejected evidence. Inspect their citations before approval.')
    snapshot=hashlib.sha256(json.dumps({'scope':req.scope,'witness':req.witness,'include':req.include,'status':req.status,'targets':targets},sort_keys=True).encode()).hexdigest()
    return {'workspace_name':matter.name,'scope':req.scope,'witness':req.witness,'status':req.status,'evidence_count':len(evidence),'question_count':len(questions),'total':len(targets),'snapshot':snapshot},evidence,questions,targets


def register_bulk_review(app):
    from . import main as db
    @app.post('/api/matters/{mid}/review/bulk-preview')
    def preview(mid:str,req:BulkReviewInput):
        with Session(db.engine) as s:return preview_review(s,mid,req)[0]

    @app.post('/api/matters/{mid}/review/bulk')
    def apply(mid:str,req:BulkReviewApply):
        if not req.reviewed:raise HTTPException(400,'Acknowledge review of the selected evidence/questions first')
        with Session(db.engine) as s:
            db.matter(s,mid)
            s.execute(select(db.Matter).where(db.Matter.id==mid).with_for_update()).scalar_one()
            preview,evidence,questions,targets=preview_review(s,mid,req,lock=True)
            if preview['snapshot']!=req.snapshot:raise HTTPException(409,'Review items changed after the preview. Refresh the selection and review it again; nothing was changed.')
            for output in evidence:output.review_status=req.status;output.revision+=1
            grouped={}
            for outline,index,q in questions:
                if outline.id not in grouped:grouped[outline.id]=(outline,[dict(item) for item in outline.payload['questions']])
                grouped[outline.id][1][index].update(review_status=req.status,reviewed_at=db.now().isoformat(),reviewed_by='local-operator')
            for outline,items in grouped.values():
                outline.payload={**outline.payload,'questions':items};outline.revision+=1
                outline.review_status='approved' if items and all(q.get('review_status')=='approved' for q in items) else 'needs_review'
            db.audit(s,mid,'bulk_work_product_review',{'scope':req.scope,'witness':req.witness,'include':req.include,'after':req.status,'snapshot':req.snapshot,'evidence_count':len(evidence),'question_count':len(questions),'before_items':targets,'wording_modified':False,'privacy_approval_modified':False})
            s.commit();return {'updated_evidence':len(evidence),'updated_questions':len(questions),'status':req.status}
