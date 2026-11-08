from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.test_workflow import client,create
from app import main
from app.witness_profiles import assess_witness,supported_date


def finding(oid,person,statement,kind='witness',date='',status='approved'):
    return {'id':oid,'kind':kind,'review_status':status,'payload':{'person':person,'statement':statement,'quote':statement,'date':date,'chunk_id':oid}}


def test_sparse_digest_deduplicates_roles_and_never_uses_first_person_as_profile():
    name='Dana Voss'
    items=[finding('a',name,'Dana Voss is Commercial Sales Director.'),finding('b',name,'Dana Voss is Commercial Sales Director.'),finding('c',name,'I reviewed your message GLN-000011. The invoice was collected late.'),finding('d',name,'I reviewed your message GLN-000011.','event')]
    profile=assess_witness(name,items,{'a':'doc1','b':'doc2','c':'doc3','d':'doc3'},{'total_passages':100,'analyzed_passages':4})
    assert profile['limited'] and profile['distinct_profile_points']==1
    assert len(profile['summary'])==1 and 'Commercial Sales Director' in profile['summary'][0]['text']
    assert 'I reviewed' not in profile['summary'][0]['text'] and 'GLN-' not in profile['summary'][0]['text']
    assert set(profile['summary'][0]['claim_ids'])=={'a','b'}
    assert not profile['dated_event_ids'] and 'cannot yet be built' in profile['chronology_notice']
    assert profile['undated_event_count']==1 and any('4 of 100' in text for text in profile['warnings'])


def test_similar_names_remain_separate_and_missing_role_is_not_invented():
    items=[finding('a','Dana Vale','Dana Vale is Associate General Counsel.'),finding('b','Dana Voss','Dana Voss is Commercial Sales Director.')]
    vale=assess_witness('Dana Vale',items,{'a':'one','b':'two'})
    assert 'Associate General Counsel' in vale['summary'][0]['text'] and 'Commercial Sales Director' not in vale['summary'][0]['text']
    assert vale['summary'][0]['claim_ids']==['a']
    unknown=assess_witness('Dana Vale',[finding('c','Dana Vale','I reviewed the message.')],{'c':'three'})
    assert unknown['summary'][0]['claim_ids']==[] and 'do not yet establish a clear role' in unknown['summary'][0]['text']


def test_chronology_uses_explicit_event_dates_only():
    items=[finding('a','Alex','Alex attended the meeting.','event','2024-01-02'),finding('b','Alex','Alex reviewed the notes.','event','Unknown'),finding('c','Alex','Alex is a controller.')]
    profile=assess_witness('Alex',items,{'a':'one','b':'two','c':'three'})
    assert profile['dated_event_ids']==['a'] and profile['undated_event_count']==1
    assert not supported_date('unknown') and not supported_date('') and supported_date('January 2, 2024')


def test_overview_excludes_bad_sources_and_tracks_extraction_separately(client):
    mid=create(client)
    text=b'Dana Vale is Associate General Counsel. Dana Voss is Commercial Sales Director.'
    client.post(f'/api/matters/{mid}/upload',files=[('files',('roles.txt',text,'text/plain'))])
    chunk=client.get(f'/api/matters/{mid}/workspace').json()['chunks'][0]
    with Session(main.engine) as s:
        run=main.Run(matter_id=mid,provider='ollama',purpose='fixture',status='completed');s.add(run);s.flush()
        for person,statement in [('Dana Vale','Dana Vale is Associate General Counsel.'),('Dana Voss','Dana Voss is Commercial Sales Director.'),('Invented','Invented is a CEO.')]:
            s.add(main.Output(matter_id=mid,run_id=run.id,kind='witness',payload={'person':person,'statement':statement,'quote':statement,'chunk_id':chunk['id']}))
        s.commit()
    response=client.get(f'/api/matters/{mid}/witnesses/overview');assert response.status_code==200
    data=response.json();assert data['coverage']=={'total_passages':1,'analyzed_passages':0}
    assert {item['name'] for item in data['witnesses']}=={'Dana Vale','Dana Voss'}
    vale=next(item for item in data['witnesses'] if item['name']=='Dana Vale')
    assert vale['similar_names']==['Dana Voss'] and 'Associate General Counsel' in vale['summary'][0]['text']
    with Session(main.engine) as s:
        s.add(main.Run(matter_id=mid,provider='ollama',purpose='extraction',status='partial',manifest=[{'id':chunk['id'],'revision':chunk['revision'],'processed':True}]))
        s.commit()
    assert client.get(f'/api/matters/{mid}/witnesses/overview').json()['coverage']['analyzed_passages']==1
    with Session(main.engine) as s:s.get(main.Document,chunk['document_id']).status='excluded';s.commit()
    data=client.get(f'/api/matters/{mid}/witnesses/overview').json()
    assert data['witnesses']==[] and data['coverage']['total_passages']==0

import pytest
@pytest.mark.parametrize('bad_text',['I reviewed your message and need additional work.','Alex attended the meeting. '*30])
def test_sparse_profile_generation_rejects_raw_first_person_and_padding(client,monkeypatch,bad_text):
    from tests.test_workproduct import seed
    from app import retrieval
    from app.workproduct import Narrative,CitedText,Issues
    mid=create(client);chunk,cid=seed(client,mid)
    async def search(s,m,query,limit=12):return {'results':[],'indexed_current':0,'total_passages':1,'warning':''}
    monkeypatch.setattr(retrieval,'search',search)
    async def generate(provider,prompt,schema):
        if schema is Narrative:return Narrative(items=[CitedText(text=bad_text,claim_ids=[cid])]),'fixture'
        if schema is Issues:return Issues(items=[]),'fixture'
        raise AssertionError('Rejected profile text should not be published or require a support pass')
    monkeypatch.setattr(main,'generate',generate)
    response=client.post(f'/api/matters/{mid}/work-product',json={'witness':'Alex','provider':'ollama','include_questions':False})
    assert response.status_code==200,response.text
    with Session(main.engine) as s:
        output=s.scalar(select(main.Output).where(main.Output.matter_id==mid,main.Output.kind=='work_product'))
        assert output.payload['profile']==[] and output.payload['profile_rejected']==1 and output.payload['evidence_assessment']['limited']
