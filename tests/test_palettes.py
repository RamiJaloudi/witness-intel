import re
from tests.test_workflow import client
from app import main
from app.palettes import palettes,contrast,selected_palette


def test_palette_catalog_and_readable_roles():
    choices=palettes()
    assert len(choices)==9 and len({p['id'] for p in choices})==9
    for p in choices:
        assert len(p['colors'])==5 and all(re.fullmatch(r'#[0-9a-f]{6}',c) for c in p['colors'])
        assert contrast(p['tokens']['primary'],p['tokens']['on-primary'])>=4.5
        assert contrast(p['tokens']['header'],'#ffffff')>=7
        assert contrast(p['tokens']['ink'],p['tokens']['page'])>=4.5
        assert contrast(p['tokens']['muted'],'#ffffff')>=4.5
        if p['id']!='classic':assert p['source'].startswith('https://www.color-hex.com/color-palette/')


def test_palette_persisted_and_invalid_values_rejected(client,monkeypatch,tmp_path):
    path=tmp_path/'settings.env';path.write_text('OTHER_SETTING=keep\n',encoding='utf-8')
    monkeypatch.setattr(main,'ENV_PATH',path);monkeypatch.delenv('UI_PALETTE',raising=False)
    assert client.get('/api/settings').json()['ui_palette']=='classic'
    assert client.get('/api/settings/palettes').json()['selected']=='classic'
    for choice in palettes():
        response=client.post('/api/settings/palette',json={'palette':choice['id']})
        assert response.status_code==200 and selected_palette()==choice['id']
        assert client.get('/api/settings').json()['ui_palette']==choice['id']
        assert choice['id'] in path.read_text() and 'OTHER_SETTING=keep' in path.read_text()
    for bad in ['unknown','url(https://example.com)','classic; color:red']:
        assert client.post('/api/settings/palette',json={'palette':bad}).status_code==400
        assert selected_palette()=='retro'
    monkeypatch.setenv('UI_PALETTE','obsolete-palette')
    assert client.get('/api/settings/palettes').json()['selected']=='classic'
