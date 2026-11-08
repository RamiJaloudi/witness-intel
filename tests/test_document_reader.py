from tests.test_workflow import client, create


def test_reader_escapes_evidence_and_downloads_original(client):
    mid = create(client)
    content = b'Alex attended. <script>alert("unsafe")</script>'
    result = client.post(f'/api/matters/{mid}/upload', files=[('files', ('evidence.txt', content, 'text/plain'))])
    assert result.status_code == 200
    did = client.get(f'/api/matters/{mid}/workspace').json()['documents'][0]['id']
    reader = client.get(f'/api/matters/{mid}/documents/{did}/read')
    assert reader.status_code == 200
    assert 'Saved extracted text' in reader.text
    assert '&lt;script&gt;' in reader.text
    assert '<script>' not in reader.text
    original = client.get(f'/api/matters/{mid}/documents/{did}/original')
    assert original.content == content
    assert 'attachment' in original.headers['content-disposition']


def test_reader_checks_workspace_and_trash(client):
    mid, other = create(client), create(client)
    client.post(f'/api/matters/{mid}/upload', files=[('files', ('evidence.txt', b'Test evidence.', 'text/plain'))])
    did = client.get(f'/api/matters/{mid}/workspace').json()['documents'][0]['id']
    for suffix in ['read', 'original']:
        assert client.get(f'/api/matters/{other}/documents/{did}/{suffix}').status_code == 404
    from app import main
    from sqlalchemy.orm import Session
    with Session(main.engine) as session:
        main.audit(session, mid, 'workspace_deleted', {})
        session.commit()
    assert client.get(f'/api/matters/{mid}/documents/{did}/read').status_code == 404


def test_pdf_original_preview_and_cited_highlight(client):
    import fitz
    from app import main
    from sqlalchemy.orm import Session
    mid = create(client)
    pdf = fitz.open(); page = pdf.new_page(); page.insert_text((72, 72), 'Alex attended the meeting. This is original document evidence.')
    raw = pdf.tobytes(); pdf.close()
    assert client.post(f'/api/matters/{mid}/upload', files=[('files', ('evidence.pdf', raw, 'application/pdf'))]).status_code == 200
    workspace = client.get(f'/api/matters/{mid}/workspace').json()
    did, cid = workspace['documents'][0]['id'], workspace['chunks'][0]['id']
    with Session(main.engine) as session:
        run = main.Run(matter_id=mid,provider='ollama',purpose='fixture',status='completed'); session.add(run); session.flush()
        claim = main.Output(matter_id=mid,run_id=run.id,kind='witness',payload={'person':'Alex','statement':'Alex attended.','quote':'Alex attended the meeting.','chunk_id':cid})
        session.add(claim);session.commit();oid=claim.id
    reader = client.get(f'/api/matters/{mid}/documents/{did}/read',params={'claim_id':oid})
    assert '<mark>Alex attended the meeting.</mark>' in reader.text
    image = client.get(f'/api/matters/{mid}/documents/{did}/page-image',params={'claim_id':oid})
    assert image.status_code == 200 and image.content.startswith(b'\x89PNG')
    assert client.get(f'/api/matters/{mid}/documents/{did}/page-image?page=99').status_code == 404
