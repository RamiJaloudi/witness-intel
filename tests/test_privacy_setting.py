from app import main
from app.evidence import Findings
from tests.test_workflow import client, create, load

def test_manual_review_off_by_default_and_no_false_approvals(client, monkeypatch):
    monkeypatch.delenv('PRIVACY_REVIEW_REQUIRED', raising=False)
    assert client.get('/api/settings').json()['privacy_review_required'] is False
    mid = create(client); load(client, mid)
    async def fake(provider, prompt, schema):
        assert 'alex@example.com' not in prompt
        return Findings(findings=[]), 'synthetic-model'
    monkeypatch.setattr(main, 'generate', fake)
    response = client.post(f'/api/matters/{mid}/analyze', json={})
    assert response.status_code == 200
    workspace = client.get(f'/api/matters/{mid}/workspace').json()
    assert not workspace['chunks'][0]['approved']
    start = next(a for a in workspace['audit'] if a['action'] == 'analysis_started')
    assert start['details']['manual_privacy_review_required'] is False
    assert start['details']['unreviewed_passages'] == 1
    monkeypatch.setenv('PRIVACY_REVIEW_REQUIRED', 'true')
    assert client.post(f'/api/matters/{mid}/analyze', json={}).status_code == 400

def test_setting_persists_without_changing_other_configuration(client, monkeypatch, tmp_path):
    path = tmp_path / '.env'; path.write_text('OTHER_SETTING=preserve\n')
    monkeypatch.setattr(main, 'ENV_PATH', path)
    assert client.post('/api/settings/privacy-review', json={'required': False}).json()['automatic_masking']
    assert 'OTHER_SETTING=preserve' in path.read_text()
    assert client.get('/api/settings').json()['privacy_review_required'] is False
    assert client.post('/api/settings/privacy-review', json={'required': True}).status_code == 200
    assert client.get('/api/settings').json()['privacy_review_required'] is True
