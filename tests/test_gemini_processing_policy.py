import pytest
import json
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.test_workflow import client,create
from tests.test_question_context import messages
from app import main,retrieval
from app.evidence import Findings,Questions,Question,SupportReview
from app.workproduct import Narrative,CitedText,Issues
from app.question_context import AttorneyEdits,EditedQuestion


@pytest.mark.parametrize('selected_provider',['gemini','openai','anthropic'])
def test_gemini_processes_all_workflows_without_privacy_approval(client,monkeypatch,selected_provider):
    monkeypatch.setenv('PRIVACY_REVIEW_REQUIRED','false')
    mid=create(client);cid,rid=messages(client,mid)
    with Session(main.engine) as s:s.get(main.Matter,mid).privacy_mode='block';s.commit()
    async def search(s,m,query,limit=12):
        with Session(main.engine) as reader:chunk_id=reader.get(main.Output,cid).payload['chunk_id']
        return {'results':[{'chunk_id':chunk_id}],'indexed_current':0,'total_passages':2,'warning':''}
    monkeypatch.setattr(retrieval,'search',search)
    providers=[]
    async def generate(provider,prompt,schema):
        providers.append(provider);assert provider==selected_provider;assert 'blair@example.com' not in prompt and 'alex@example.com' not in prompt
        body=json.loads(prompt)
        if schema is Findings:return Findings(findings=[]),'fixture-gemini'
        if schema is Narrative:return Narrative(items=[CitedText(text='Blair reviewed a message and requested additional work.',claim_ids=[cid])]),'fixture-gemini'
        if schema is Issues:return Issues(items=[]),'fixture-gemini'
        if schema is Questions:return Questions(questions=[Question(question='What additional work did you request after reviewing DOC-000001?',purpose='Follow-up',claim_ids=[cid])]),'fixture-gemini'
        if schema is AttorneyEdits:return AttorneyEdits(questions=[EditedQuestion(source_index=q['source_index'],question=q['question']) for q in body['questions']]),'fixture-gemini'
        if schema is SupportReview:return SupportReview(supported_indices=list(range(len(body.get('candidates',[]))))),'fixture-gemini'
        raise AssertionError(schema)
    monkeypatch.setattr(main,'generate',generate)
    result=client.post(f'/api/matters/{mid}/work-product',json={'witness':'Blair','provider':selected_provider,'instructions':'Contact blair@example.com'})
    assert result.status_code==200,result.text
    request={'witness':'Blair','provider':selected_provider,'alignment':'unknown','proceeding':'deposition','examination':'direct','style':'open-ended','topics':'Contact blair@example.com'}
    result=client.post(f'/api/matters/{mid}/questions',json=request)
    assert result.status_code==200,result.text
    with Session(main.engine) as s:oid=s.scalar(select(main.Output.id).where(main.Output.matter_id==mid,main.Output.kind=='questions'))
    result=client.post(f'/api/matters/{mid}/outlines/{oid}/contextualize',json={'provider':selected_provider})
    assert result.status_code==200,result.text
    settings=client.get('/api/settings').json()
    assert not settings['privacy_review_required'] and not settings['gemini_privacy_approval_required']
    with Session(main.engine) as s:
        chunks=list(s.scalars(select(main.Chunk).join(main.Document).where(main.Document.matter_id==mid)))
        assert all(not c.approved for c in chunks)
        starts=list(s.scalars(select(main.Audit).where(main.Audit.matter_id==mid,main.Audit.action.in_(['question_generation_started','work_product_started','question_context_revision_started']))))
        assert starts and all(not a.details['manual_privacy_review_required'] for a in starts)
    assert providers and set(providers)=={selected_provider}
