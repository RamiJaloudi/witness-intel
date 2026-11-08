from sqlalchemy import select
from sqlalchemy.orm import Session
from app import main
from tests.test_workflow import client, create, load

def test_delete_is_scoped_confirmed_and_recoverable(client):
    mid, other = create(client), create(client)
    workspace = load(client, mid)
    doc = workspace['documents'][0]; chunk = workspace['chunks'][0]
    preview = client.get(f'/api/matters/{mid}/deletion-preview').json()
    assert preview['documents'] == 1 and preview['passages'] == 1
    assert client.post(f'/api/matters/{mid}/delete', json={'confirmation_name': 'wrong'}).status_code == 400
    assert client.post(f'/api/matters/{mid}/delete', json={'confirmation_name': 'Synthetic matter'}).json()['recoverable']
    ids = {m['id'] for m in client.get('/api/matters').json()}
    assert mid not in ids and other in ids
    assert client.get(f'/api/matters/{mid}/workspace').status_code == 404
    assert client.get(f'/api/matters/{mid}/chunks/{chunk["id"]}').status_code == 404
    assert client.post(f'/api/matters/{mid}/chunks/{chunk["id"]}/review', json={'approved': True, 'names_reviewed': True}).status_code == 404
    assert mid in {m['id'] for m in client.get('/api/workspaces/trash').json()}
    assert (main.ROOT / mid / doc['id'] / 'original.txt').exists()
    assert client.post(f'/api/matters/{mid}/restore').status_code == 200
    assert mid in {m['id'] for m in client.get('/api/matters').json()}
    restored = client.get(f'/api/matters/{mid}/workspace').json()
    assert len(restored['documents']) == 1
    assert {a['action'] for a in restored['audit']} >= {'workspace_deleted', 'workspace_restored'}

def test_running_analysis_blocks_deletion(client):
    mid = create(client)
    with Session(main.engine) as s:
        s.add(main.Run(matter_id=mid, provider='gemini', purpose='synthetic', status='running')); s.commit()
    assert client.post(f'/api/matters/{mid}/delete', json={'confirmation_name': 'Synthetic matter'}).status_code == 409
