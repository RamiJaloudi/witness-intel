import io
import json
import zipfile
from pathlib import Path
from app import main
from tests.test_workflow import client, create, load


def test_backup_roundtrip_restores_new_ids_and_evidence(client, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','SECRET_NOT_IN_BACKUP')
    mid = create(client); before = load(client, mid)
    result = client.post('/api/backups', json={'matter_id':mid})
    assert result.status_code == 200, result.text
    backup = client.get(result.json()['download_url'])
    assert backup.status_code == 200
    with zipfile.ZipFile(io.BytesIO(backup.content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['credentials_included'] is False
        assert 'SECRET_NOT_IN_BACKUP' not in json.dumps(manifest)
    preview = client.post('/api/backups/preview', files={'file':('backup.zip',backup.content,'application/zip')})
    assert preview.status_code == 200, preview.text
    restored = client.post('/api/backups/restore', json={'token':preview.json()['token'],'confirmation':'RESTORE AS NEW WORKSPACES'})
    assert restored.status_code == 200, restored.text
    restored_id = restored.json()['workspace_ids'][0]
    assert restored_id != mid
    after = client.get(f'/api/matters/{restored_id}/workspace').json()
    assert len(after['documents']) == len(before['documents'])
    assert after['documents'][0]['id'] != before['documents'][0]['id']
    assert after['chunks'][0]['text'] == before['chunks'][0]['text']
    original = client.get(f'/api/matters/{restored_id}/documents/{after["documents"][0]["id"]}/original')
    assert original.content == b'Alex attended the meeting. Contact alex@example.com.'
    assert client.post('/api/backups/restore', json={'token':preview.json()['token'],'confirmation':'RESTORE AS NEW WORKSPACES'}).status_code == 409
    assert client.get(f'/api/matters/{mid}/workspace').json()['documents'][0]['id'] == before['documents'][0]['id']


def test_restore_rejects_unsafe_archive_and_missing_original(client):
    mid = create(client); before = load(client, mid)
    data = io.BytesIO()
    with zipfile.ZipFile(data,'w') as archive:
        archive.writestr('../outside.txt','unsafe')
        archive.writestr('manifest.json','{}')
    assert client.post('/api/backups/preview',files={'file':('bad.zip',data.getvalue(),'application/zip')}).status_code == 400
    original = next((main.ROOT / mid / before['documents'][0]['id']).glob('original*'))
    original.write_bytes(b'changed evidence')
    assert client.post('/api/backups',json={'matter_id':mid}).status_code == 409


def test_restore_applies_opted_in_settings_and_keeps_server_keys(client,monkeypatch,tmp_path):
    from dotenv import dotenv_values
    import os
    config=tmp_path/'server.env';config.write_text('OPENAI_API_KEY=local-key-kept\nUI_PALETTE=classic\n',encoding='utf-8')
    monkeypatch.setattr(main,'ENV_PATH',config)
    from app.providers import PROVIDERS
    for provider in PROVIDERS.values():
        monkeypatch.setenv(provider['model_env'],os.environ.get(provider['model_env'],provider['default_model']))
    monkeypatch.setenv('UI_PALETTE','teal')
    mid=create(client);load(client,mid)
    result=client.post('/api/backups',json={'matter_id':mid}).json()
    backup=client.get(result['download_url']).content
    monkeypatch.setenv('UI_PALETTE','classic')
    preview=client.post('/api/backups/preview',files={'file':('backup.zip',backup,'application/zip')}).json()
    restored=client.post('/api/backups/restore',json={'token':preview['token'],'confirmation':'RESTORE AS NEW WORKSPACES','apply_settings':True})
    assert restored.status_code==200,restored.text
    assert restored.json()['settings_applied'] is True
    assert os.environ['UI_PALETTE']=='teal'
    assert dotenv_values(config)['OPENAI_API_KEY']=='local-key-kept'


def test_backup_progress_finishes_and_is_listed(client):
    import uuid
    mid=create(client);load(client,mid);operation=str(uuid.uuid4())
    result=client.post('/api/backups',json={'matter_id':mid,'operation_id':operation})
    assert result.status_code==200
    progress=client.get('/api/backups/progress/'+operation).json()
    assert progress=={'phase':'Completed','done':1,'total':1}
    assert client.get('/api/backups').json()['backups'][0]['backup_id']==result.json()['backup_id']
