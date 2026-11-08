import pytest
from app.question_context import AttorneyEdits,EditedQuestion
import json
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.test_workflow import client,create,load
from app import main,retrieval
from app.workproduct import Narrative,CitedText,Issues,Issue
from app.evidence import Findings,SupportReview,Questions,Question
from app.providers import ProviderError


def seed(client,mid):
    chunk=load(client,mid)['chunks'][0]
    with Session(main.engine) as s:
        run=main.Run(matter_id=mid,provider='ollama',purpose='synthetic',status='completed');s.add(run);s.flush()
        claim=main.Output(matter_id=mid,run_id=run.id,kind='witness',payload={'person':'Alex','statement':'Alex attended the meeting.','quote':'Alex attended the meeting.','chunk_id':chunk['id']});s.add(claim);s.commit();return chunk,claim.id


def test_masked_embeddings_scoped_stale_and_hybrid(client,monkeypatch):
    mid=create(client);chunk,cid=seed(client,mid);other=create(client);seed(client,other)
    seen=[]
    async def embed(texts,query=False):
        seen.extend(texts);return [[1.0]+[0.0]*767 for t in texts]
    monkeypatch.setattr(retrieval,'embed',embed)
    result=client.post(f'/api/matters/{mid}/search/index',json={'limit':100})
    assert result.status_code==200 and result.json()['indexed']==1
    assert all('Alex' not in text and 'alex@example.com' not in text for text in seen)
    result=client.post(f'/api/matters/{mid}/search',json={'query':'Alex meeting'}).json()
    assert result['indexed_current']==1 and [r['chunk_id'] for r in result['results']]==[chunk['id']]
    with Session(main.engine) as s:
        c=s.get(main.Chunk,chunk['id']);c.revision+=1;s.commit()
    result=client.post(f'/api/matters/{mid}/search',json={'query':'meeting'}).json()
    assert result['indexed_current']==0 and result['results'][0]['chunk_id']==chunk['id']
    # Workspace purge must remove vector rows before their source chunks.
    assert client.post(f'/api/matters/{mid}/delete',json={'confirmation_name':'Synthetic matter'}).status_code==200
    preview=client.post('/api/workspaces/trash/purge-preview',json={'workspace_ids':[mid]}).json()
    assert preview['counts']['embeddings']==1
    result=client.post('/api/workspaces/trash/purge',json={'workspace_ids':[mid],'confirmation':preview['confirmation']})
    assert result.status_code==200
    with Session(main.engine) as s:assert not list(s.scalars(select(main.Embedding).where(main.Embedding.matter_id==mid)))


def test_workproduct_draft_verification_and_checkpoints(client,monkeypatch):
    mid=create(client);chunk,cid=seed(client,mid)
    async def search(s,m,query,limit=12):return {'results':[{'chunk_id':chunk['id']}],'indexed_current':0,'total_passages':1,'warning':'keyword only','complete_corpus_review':False}
    monkeypatch.setattr(retrieval,'search',search)
    async def generate(provider,prompt,schema):
        body=json.loads(prompt)
        if schema is AttorneyEdits:return AttorneyEdits(questions=[EditedQuestion(source_index=q['source_index'],question=q['question']) for q in body['questions']]),'test-model'
        if schema is Findings:return Findings(findings=[]),'test-model'
        if schema is Narrative:return Narrative(items=[CitedText(text='Alex attended the meeting.',claim_ids=[cid],category='possible_issue' if 'possible factual' in body['task'] else 'supported_fact'),CitedText(text='Unsupported claim.',claim_ids=['foreign-id'])]),'test-model'
        if schema is Issues:return Issues(items=[Issue(question='What was Alex personally able to observe at the meeting?',factual_basis='Alex attended the meeting.',why_it_matters='Attendance alone does not establish personal knowledge of every discussion.',claim_ids=[cid],issue_type='factual_issue')]),'test-model'
        if schema is SupportReview:return SupportReview(supported_indices=[0]),'test-model'
        if schema is Questions:return Questions(questions=[Question(question='Did you attend the meeting?',purpose='Attendance',claim_ids=[cid]),Question(question='You stole money?',purpose='Unsupported',claim_ids=[cid])]),'test-model'
        raise AssertionError(schema)
    monkeypatch.setattr(main,'generate',generate)
    response=client.post(f'/api/matters/{mid}/work-product',json={'witness':'Alex','provider':'ollama'})
    assert response.status_code==200,response.text
    workspace=client.get(f'/api/matters/{mid}/workspace').json()
    product=next(o for o in workspace['outputs'] if o['kind']=='work_product')
    assert product['review_status']=='needs_review'
    assert len(product['payload']['profile'])==1 and len(product['payload']['issues'])==1
    assert len(product['payload']['completed_stages'])==5
    outline=next(o for o in workspace['outputs'] if o['kind']=='questions')
    assert len(outline['payload']['questions'])==1
    assert outline['payload']['questions'][0]['review_status']=='needs_review'
    assert client.get(f'/api/matters/{mid}/work-product/status').json()['status']=='completed'
    assert client.post(f'/api/matters/{mid}/outputs/{product["id"]}/review',json={'status':'approved'}).status_code==200
    client.post(f'/api/matters/{mid}/outputs/{cid}/review',json={'status':'rejected'})
    assert client.post(f'/api/matters/{mid}/outputs/{product["id"]}/review',json={'status':'approved'}).status_code==409
    with Session(main.engine) as s:assert s.get(main.Output,cid).review_status=='rejected'


@pytest.mark.parametrize('selected_provider',['gemini','openai','anthropic'])
def test_cloud_workproduct_requires_name_review_when_enabled(client,monkeypatch,selected_provider):
    mid=create(client);chunk,cid=seed(client,mid)
    async def search(s,m,query,limit=12):return {'results':[{'chunk_id':chunk['id']}],'indexed_current':0,'total_passages':1,'warning':''}
    monkeypatch.setattr(retrieval,'search',search)
    async def forbidden(*args):raise AssertionError('No cloud egress permitted')
    monkeypatch.setattr(main,'generate',forbidden)
    response=client.post(f'/api/matters/{mid}/work-product',json={'witness':'Alex','provider':selected_provider})
    assert response.status_code==502 and 'privacy review' in response.json()['detail']
    assert client.get(f'/api/matters/{mid}/work-product/status').json()['status']=='failed'
    request={'witness':'Alex','provider':selected_provider,'alignment':'unknown','proceeding':'deposition','examination':'direct','style':'open-ended'}
    assert client.post(f'/api/matters/{mid}/questions',json=request).status_code==400


def test_partial_stages_survive_provider_failure(client,monkeypatch):
    mid=create(client);chunk,cid=seed(client,mid)
    async def search(s,m,query,limit=12):return {'results':[],'indexed_current':0,'total_passages':1,'warning':''}
    monkeypatch.setattr(retrieval,'search',search)
    async def failed(*args):raise ProviderError('Synthetic model unavailable')
    monkeypatch.setattr(main,'generate',failed)
    result=client.post(f'/api/matters/{mid}/work-product',json={'witness':'Alex','provider':'ollama'})
    assert result.status_code==502
    status=client.get(f'/api/matters/{mid}/work-product/status').json()
    assert status['status']=='partial' and status['stages'][0]['stage']=='corpus_research'
