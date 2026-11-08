import pytest
import asyncio
import json
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.test_workflow import client,create
from app import main
from app.question_context import document_context,AttorneyEdits,EditedQuestion,contextual_sequences
from app.evidence import SupportReview


def messages(client,mid):
    first=b'From: Alex <alex@example.com>\nTo: Blair <blair@example.com>\nDate: Tue, 02 Jan 2024 09:00:00 +0000\nSubject: Ledger controls\nContent-Type: text/plain; charset=utf-8\n\nAlex proposed reviewing ledger controls.\n'
    reply=b'From: Blair <blair@example.com>\nTo: Alex <alex@example.com>\nDate: Wed, 03 Jan 2024 10:00:00 +0000\nSubject: Re: Ledger controls\nContent-Type: text/plain; charset=utf-8\n\nBlair reviewed DOC-000001 and requested more work.\n'
    for name,body in [('DOC-000001.eml',first),('DOC-000002.eml',reply)]:
        assert client.post(f'/api/matters/{mid}/upload',files=[('files',(name,body,'message/rfc822'))]).status_code==200
    with Session(main.engine) as s:
        doc=s.scalar(select(main.Document).where(main.Document.matter_id==mid,main.Document.name=='DOC-000002.eml'))
        chunk=s.scalar(select(main.Chunk).where(main.Chunk.document_id==doc.id))
        run=main.Run(matter_id=mid,provider='ollama',purpose='fixture',status='completed');s.add(run);s.flush()
        claim=main.Output(matter_id=mid,run_id=run.id,kind='witness',payload={'person':'Blair','statement':'Blair reviewed DOC-000001 and requested more work.','quote':'Blair reviewed DOC-000001 and requested more work.','chunk_id':chunk.id})
        s.add(claim);s.commit();return claim.id,run.id


def test_context_resolves_referenced_message_not_reply(client):
    mid=create(client);cid,rid=messages(client,mid)
    with Session(main.engine) as s:
        context,unresolved=document_context(s,mid,[s.get(main.Output,cid)])
        original=next(c for c in context if c['document_reference']=='DOC-000001')
        reply=next(c for c in context if c['document_reference']=='DOC-000002')
        assert '02 Jan 2024' in original['headers']['date'] and '03 Jan 2024' in reply['headers']['date']
        assert original['headers']['from'].startswith('Alex') and reply['headers']['from'].startswith('Blair')
        assert 'alex@example.com' not in json.dumps(context)
        assert original['quote'] in s.get(main.Chunk,original['chunk_id']).text
        assert not unresolved


def test_context_does_not_resolve_other_matter_reference(client):
    other=create(client);messages(client,other);mid=create(client)
    with Session(main.engine) as s:
        doc=main.Document(matter_id=mid,name='DOC-000003.eml',sha256='synthetic',size=1,status='needs_privacy_review');s.add(doc);s.flush()
        chunk=main.Chunk(document_id=doc.id,locator='message 1; segment 1',text='From: Blair\nTo: Alex\nSubject: Follow-up\nBlair mentioned DOC-000001.',detections=[]);s.add(chunk);s.flush()
        run=main.Run(matter_id=mid,provider='ollama',purpose='fixture',status='completed');s.add(run);s.flush()
        claim=main.Output(matter_id=mid,run_id=run.id,kind='witness',payload={'person':'Blair','chunk_id':chunk.id,'quote':'Blair mentioned DOC-000001.','statement':'Blair mentioned DOC-000001.'});s.add(claim);s.flush()
        contexts,unresolved=document_context(s,mid,[claim])
        assert 'DOC-000001' in unresolved
        assert all(c['document_id']==doc.id for c in contexts)
        assert all('date' not in c['headers'] for c in contexts)


def test_context_keeps_mbox_messages_separate_and_ignores_forwarded_headers(client):
    mid=create(client)
    with Session(main.engine) as s:
        doc=main.Document(matter_id=mid,name='bundle.mbox',sha256='synthetic',size=1,status='needs_privacy_review');s.add(doc);s.flush()
        run=main.Run(matter_id=mid,provider='ollama',purpose='fixture',status='completed');s.add(run);s.flush();claims=[]
        for i,name in [(1,'Alex'),(2,'Blair')]:
            chunk=main.Chunk(document_id=doc.id,locator=f'message {i}; segment 1',text=f'From: {name}\nTo: Recipient\nSubject: Item {i}\nBody begins.\nFrom: Wrong forwarded sender\n{name} attended a meeting.',detections=[]);s.add(chunk);s.flush()
            claim=main.Output(matter_id=mid,run_id=run.id,kind='witness',payload={'person':name,'chunk_id':chunk.id,'quote':f'{name} attended a meeting.','statement':f'{name} attended a meeting.'});s.add(claim);claims.append(claim)
        s.flush();contexts,unresolved=document_context(s,mid,claims)
        assert {c['headers']['from'] for c in contexts}=={'Alex','Blair'}
        assert {c['locator'] for c in contexts}=={'message 1; segment 1','message 2; segment 1'}


def test_contextualizer_splits_pending_preserves_protected_and_audits_original(client,monkeypatch):
    mid=create(client);cid,rid=messages(client,mid)
    original='What was the context of your review of DOC-000001?'
    with Session(main.engine) as s:
        outline=main.Output(matter_id=mid,run_id=rid,kind='questions',payload={'settings':{'witness':'Blair','alignment':'unknown','style':'open-ended'},'questions':[{'question':'Approved wording','purpose':'Protected','claim_ids':[cid],'review_status':'approved'},{'question':original,'purpose':'Context','claim_ids':[cid],'review_status':'needs_review'},{'question':'Rejected wording','purpose':'Protected','claim_ids':[cid],'review_status':'rejected'}]});s.add(outline);s.commit();oid=outline.id
    async def generate(provider,prompt,schema):
        body=json.loads(prompt)
        if schema is AttorneyEdits:
            context=next(c for c in body['document_contexts'] if c['document_reference']=='DOC-000001')
            return AttorneyEdits(questions=[EditedQuestion(source_index=0,question="Do you recognize DOC-000001, Alex's January 2 email about ledger controls?",additional_context_ids=[context['id']]),EditedQuestion(source_index=0,question='What additional work did you request after reviewing that email?',additional_context_ids=[context['id']])]),'fixture'
        return SupportReview(supported_indices=list(range(len(body['candidates'])))),'fixture'
    monkeypatch.setattr(main,'generate',generate)
    response=client.post(f'/api/matters/{mid}/outlines/{oid}/contextualize',json={'provider':'ollama'})
    assert response.status_code==200,response.text
    with Session(main.engine) as s:
        outline=s.get(main.Output,oid);qs=outline.payload['questions']
        assert len(qs)==4 and qs[0]['question']=='Approved wording' and qs[-1]['question']=='Rejected wording'
        assert all(q['original_question']==original and q['wording_history'][0]['question']==original for q in qs[1:3])
        assert all(cid in q['claim_ids'] and q['review_status']=='needs_review' and q['sequence_total']==2 for q in qs[1:3])
        assert all(len(q['question'].split())<=25 for q in qs[1:3])
        audit=s.scalar(select(main.Audit).where(main.Audit.action=='question_context_revision',main.Audit.matter_id==mid))
        assert audit.details['before_questions'][0]['question']==original


@pytest.mark.parametrize('selected_provider',['gemini','openai','anthropic'])
def test_question_context_blocks_unreviewed_cloud_egress_when_review_enabled(client,monkeypatch,selected_provider):
    mid=create(client);cid,rid=messages(client,mid)
    with Session(main.engine) as s:
        claim=s.get(main.Output,cid);s.get(main.Chunk,claim.payload['chunk_id']).approved=True;s.commit()
    async def forbidden(*args):raise AssertionError('No cloud egress')
    monkeypatch.setattr(main,'generate',forbidden)
    response=client.post(f'/api/matters/{mid}/questions',json={'provider':selected_provider,'witness':'Blair','alignment':'unknown','proceeding':'deposition','examination':'direct','style':'open-ended'})
    assert response.status_code==502 and 'privacy review' in response.json()['detail']

def test_invalid_context_ids_cannot_change_original_draft(client,monkeypatch):
    mid=create(client);cid,rid=messages(client,mid)
    original='What additional work did you request?'
    with Session(main.engine) as s:
        outline=main.Output(matter_id=mid,run_id=rid,kind='questions',payload={'settings':{'witness':'Blair'},'questions':[{'question':original,'purpose':'Context','claim_ids':[cid],'review_status':'needs_review'}]});s.add(outline);s.commit();oid=outline.id
    async def generate(provider,prompt,schema):
        if schema is AttorneyEdits:return AttorneyEdits(questions=[EditedQuestion(source_index=0,question='What happened in the other matter?',additional_context_ids=['foreign-context'])]),'fixture'
        raise AssertionError('Invalid context edit must not reach acceptance')
    monkeypatch.setattr(main,'generate',generate)
    response=client.post(f'/api/matters/{mid}/outlines/{oid}/contextualize',json={'provider':'ollama'})
    assert response.status_code==200 and response.json()['rewritten']==0
    with Session(main.engine) as s:
        question=s.get(main.Output,oid).payload['questions'][0]
        assert question['question']==original and question['claim_ids']==[cid] and question['context_review_note']


def test_concurrent_attorney_change_is_not_overwritten(client,monkeypatch):
    mid=create(client);cid,rid=messages(client,mid)
    with Session(main.engine) as s:
        outline=main.Output(matter_id=mid,run_id=rid,kind='questions',payload={'settings':{'witness':'Blair'},'questions':[{'question':'Original pending question?','purpose':'Context','claim_ids':[cid],'review_status':'needs_review'}]});s.add(outline);s.commit();oid=outline.id
    async def generate(provider,prompt,schema):
        if schema is AttorneyEdits:
            response=await asyncio.to_thread(client.post,f'/api/matters/{mid}/outlines/{oid}/questions/0/review',json={'question':'Attorney revised wording?','status':'approved'})
            assert response.status_code==200
            return AttorneyEdits(questions=[EditedQuestion(source_index=0,question='Model revised wording?')]),'fixture'
        return SupportReview(supported_indices=[0]),'fixture'
    monkeypatch.setattr(main,'generate',generate)
    response=client.post(f'/api/matters/{mid}/outlines/{oid}/contextualize',json={'provider':'ollama'})
    assert response.status_code==409
    with Session(main.engine) as s:
        question=s.get(main.Output,oid).payload['questions'][0]
        assert question['question']=='Attorney revised wording?' and question['review_status']=='approved'


def test_deterministic_framing_uses_verified_context_once_and_splits():
    context={'id':'context','document_reference':'DOC-000001','headers':{'from':'Alex <[EMAIL_x]>','to':'Blair <[EMAIL_y]>','date':'Tue, 02 Jan 2024 09:00:00 +0000'}}
    source=[{'question':'What was the context of your review of message DOC-000001, and what specific point required further work?','purpose':'Context','claim_ids':['claim']},{'question':'What steps did you take after reviewing message DOC-000001?','purpose':'Follow-up','claim_ids':['claim']}]
    framed=contextual_sequences(source,[context])
    assert len(framed[0])==3 and len(framed[1])==1
    assert 'Alex' in framed[0][0]['question'] and 'Blair' in framed[0][0]['question'] and 'January 2, 2024' in framed[0][0]['question']
    assert framed[0][1]['question']=='What prompted you to review that email?'
    assert all(len(q['question'].split())<=25 and set(q['claim_ids'])=={'claim','context'} for seq in framed.values() for q in seq)

def test_followups_require_their_accepted_document_foundation():
    from app.question_context import edit_questions
    context={'id':'context','document_reference':'DOC-000001','headers':{'from':'Alex','to':'Blair','date':'Tue, 02 Jan 2024 09:00:00 +0000'},'quote':'From: Alex\nTo: Blair\nDate: Tue, 02 Jan 2024 09:00:00 +0000'}
    questions=[{'question':'What was the context of your review of message DOC-000001, and what point required further work?','purpose':'Context','claim_ids':['claim']}]
    async def generate(provider,prompt,schema):
        if schema is AttorneyEdits:return AttorneyEdits(questions=[]),'fixture'
        return SupportReview(supported_indices=[1,2]),'fixture'
    result,rejected=asyncio.run(edit_questions(generate,'ollama',questions,[{'id':'claim','quote':'Blair reviewed DOC-000001.'}],[context],{}))
    assert result=={} and rejected>=2

def test_editor_cannot_replace_substantive_issue_with_unrelated_exhibit():
    from app.question_context import edit_questions
    source=[{'question':'What responsibilities did your position involve?','purpose':'Role','claim_ids':['claim']}]
    context={'id':'context','document_reference':'DOC-000001','headers':{'from':'Alex'},'quote':'From: Alex'}
    async def generate(provider,prompt,schema):
        if schema is AttorneyEdits:return AttorneyEdits(questions=[EditedQuestion(source_index=0,question='Do you recognize DOC-000001?',additional_context_ids=['D1'])]),'fixture'
        raise AssertionError('Unrelated exhibit substitution must be rejected before verification')
    result,rejected=asyncio.run(edit_questions(generate,'ollama',source,[{'id':'claim','quote':'Alex was a controller.'}],[context],{}))
    assert result=={} and rejected==1


def test_editor_removes_duplicate_questions():
    from app.question_context import edit_questions
    source=[{'question':'What prompted your review?','purpose':'Review','claim_ids':['claim']},{'question':'Why did you review it?','purpose':'Review','claim_ids':['claim']}]
    async def generate(provider,prompt,schema):
        if schema is AttorneyEdits:return AttorneyEdits(questions=[EditedQuestion(source_index=i,question='What prompted you to review that email?') for i in [0,1]]),'fixture'
        return SupportReview(supported_indices=[0,1]),'fixture'
    result,rejected=asyncio.run(edit_questions(generate,'ollama',source,[{'id':'claim','quote':'Alex reviewed the email.'}],[],{}))
    assert len(result)==1 and rejected==1
