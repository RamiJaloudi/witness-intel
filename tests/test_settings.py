import os
import pytest
from fastapi.testclient import TestClient
from app import main

@pytest.mark.parametrize('provider,path,status_field',[
    ('gemini','/api/settings/gemini-key','gemini_key_configured'),
    ('openai','/api/settings/providers/openai/key','openai_key_configured'),
    ('anthropic','/api/settings/providers/anthropic/key','anthropic_key_configured'),
])
def test_key_saved_locally_without_disclosure(tmp_path, monkeypatch,provider,path,status_field):
    monkeypatch.setenv('INDEXING_MODE','manual')
    async def idle(self):pass
    monkeypatch.setattr(main.FullCorpusManager,'start',idle)
    env = tmp_path / '.env'
    env.write_text('OTHER_SETTING=keep\n')
    monkeypatch.setattr(main, 'ENV_PATH', env)
    monkeypatch.setattr(main, 'ROOT', tmp_path / 'data')
    env_name=main.PROVIDERS[provider]['key_env']
    monkeypatch.setenv(env_name, '')
    key = 'synthetic.test/key+not=a-real-key'
    with TestClient(main.app) as client:
        assert not client.get('/api/settings').json()['providers'][provider]['key_configured']
        response = client.post(path, json={'api_key': key})
        assert response.status_code == 200 and key not in response.text
        assert os.environ[env_name] == key
        assert 'OTHER_SETTING=keep' in env.read_text() and key in env.read_text()
        settings = client.get('/api/settings')
        assert settings.json()['providers'][provider]['key_configured'] and key not in settings.text
        invalid = 'sensitive key with spaces'
        response = client.post(path, json={'api_key': invalid})
        assert response.status_code == 400 and invalid not in response.text
        assert client.post(path, json={'api_key': key}, headers={'origin': 'https://example.com'}).status_code == 403
        for payload in [{'api_key':{'secret':key}},{'api_key':key+'\nunsafe'},[],{}]:
            response=client.post(path,json=payload)
            assert response.status_code==400 and key not in response.text
