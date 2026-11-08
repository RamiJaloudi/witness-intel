from app.extract import extract
from tests.test_workflow import client, create, load

def test_xml_text_order_and_dtd_rejection(tmp_path):
    import pytest
    p = tmp_path / 'records.xml'; p.write_text('<root>Before<name>Alex</name>After<event>Meeting</event>End</root>')
    text = ' '.join(t for _, t in extract(p))
    assert text.index('Before') < text.index('Alex') < text.index('After') < text.index('Meeting') < text.index('End')
    p.write_text('<!DOCTYPE root [<!ENTITY x "test">]><root>&x;</root>')
    with pytest.raises(ValueError, match='DTD'): list(extract(p))

def test_metadata_workspace_and_scoped_source_fetch(client):
    mid = create(client); w = load(client, mid)
    chunk, doc = w['chunks'][0], w['documents'][0]
    metadata = client.get(f'/api/matters/{mid}/workspace?include_text=false').json()
    assert 'text' not in metadata['chunks'][0]
    assert client.get(f'/api/matters/{mid}/documents/{doc["id"]}/chunks').json()[0]['text'] == chunk['text']
    assert client.get(f'/api/matters/{mid}/chunks/{chunk["id"]}').json()['text'] == chunk['text']
    other = create(client)
    assert client.get(f'/api/matters/{other}/chunks/{chunk["id"]}').status_code == 404
