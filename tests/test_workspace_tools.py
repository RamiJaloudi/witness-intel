import copy
from sqlalchemy import select
from sqlalchemy.orm import Session
from app import main
from tests.test_workflow import client, create, load


def findings(client):
    mid = create(client); data = load(client, mid)
    with Session(main.engine) as session:
        run = main.Run(matter_id=mid, provider='ollama', model='test', purpose='extraction', status='completed')
        session.add(run); session.flush()
        outputs = [main.Output(matter_id=mid, run_id=run.id, kind='witness', payload={'person': name, 'statement': f'{name} attended the meeting.', 'quote': 'Alex attended the meeting.', 'chunk_id': data['chunks'][0]['id']}) for name in ['Alex', 'A. Example']]
        session.add_all(outputs); session.commit()
        return mid, [output.id for output in outputs]


def test_identity_merge_and_undo_preserve_source_and_review(client):
    mid, ids = findings(client)
    request = {'alias':'A. Example','canonical':'Alex','confirmed':True}
    result = client.post(f'/api/matters/{mid}/identities/merge', json=request)
    assert result.status_code == 200
    merge_id = result.json()['merge_id']
    with Session(main.engine) as session:
        output = session.get(main.Output, ids[1])
        assert output.payload['person'] == 'Alex'
        assert output.payload['quote'] == 'Alex attended the meeting.'
        assert output.review_status == 'needs_review'
    assert client.post(f'/api/matters/{mid}/identities/{merge_id}/undo', json={}).status_code == 200
    with Session(main.engine) as session:
        assert session.get(main.Output, ids[1]).payload['person'] == 'A. Example'
    assert client.post(f'/api/matters/{mid}/identities/{merge_id}/undo', json={}).status_code == 409


def test_merge_undo_does_not_overwrite_later_edits(client):
    mid, ids = findings(client)
    merge = client.post(f'/api/matters/{mid}/identities/merge', json={'alias':'A. Example','canonical':'Alex','confirmed':True}).json()
    with Session(main.engine) as session:
        output = session.get(main.Output, ids[1]); output.payload = {**output.payload, 'statement':'Later attorney correction'}; session.commit()
    assert client.post(f'/api/matters/{mid}/identities/{merge["merge_id"]}/undo', json={}).status_code == 409
    with Session(main.engine) as session:
        assert session.get(main.Output, ids[1]).payload['statement'] == 'Later attorney correction'


def test_search_review_case_and_saved_views_are_scoped(client):
    mid, ids = findings(client); other = create(client)
    assert client.get(f'/api/matters/{mid}/search', params={'q':'Alex'}).json()['results']
    assert client.get(f'/api/matters/{other}/search', params={'q':'Alex'}).json()['results'] == []
    queue = client.get(f'/api/matters/{mid}/review-queue').json()
    assert queue['total'] == 2
    assert client.get(f'/api/matters/{other}/review-queue').json()['total'] == 0
    issue = {'title':'Who attended?', 'question':'Resolve the attribution.', 'supporting_claim_ids':[ids[0]], 'contradicting_claim_ids':[ids[1]], 'witness_names':['Alex']}
    assert client.post(f'/api/matters/{other}/case/issues', json=issue).status_code == 400
    result = client.post(f'/api/matters/{mid}/case/issues', json=issue)
    assert result.status_code == 200
    saved = result.json()
    assert client.post(f'/api/matters/{mid}/case/issues/{saved["id"]}', json={**issue,'revision':99}).status_code == 409
    view = {'name':'Pending Alex','section':'Review','values':{'reviewSearch':'Alex','reviewStatus':'needs_review'}}
    assert client.post(f'/api/matters/{mid}/views', json=view).status_code == 200
    assert client.get(f'/api/matters/{mid}/views').json()[0]['name'] == view['name']
    assert client.get(f'/api/matters/{other}/views').json() == []


def test_review_optimistic_revision_and_attention(client):
    mid, ids = findings(client)
    assert client.post(f'/api/matters/{mid}/outputs/{ids[0]}/review', json={'status':'approved','revision':99}).status_code == 409
    assert client.post(f'/api/matters/{mid}/outputs/{ids[0]}/review', json={'status':'approved','revision':1}).status_code == 200
    assert client.get(f'/api/matters/{mid}/attention').json()['pending_findings_and_products'] == 1
