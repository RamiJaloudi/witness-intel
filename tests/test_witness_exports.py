import csv
import io
import json
import zipfile

from docx import Document
from sqlalchemy.orm import Session

from app import main
from tests.test_workflow import client, create, load


def seed_export(client):
    mid = create(client)
    chunk = load(client, mid)['chunks'][0]
    with Session(main.engine) as session:
        run = main.Run(matter_id=mid, provider='ollama', purpose='synthetic', status='completed')
        session.add(run); session.flush()
        finding = main.Output(matter_id=mid, run_id=run.id, kind='witness', review_status='approved', payload={
            'person': 'Alex', 'statement': 'Alex attended the meeting.', 'quote': 'Alex attended the meeting.', 'chunk_id': chunk['id']})
        session.add(finding); session.flush()
        session.add(main.Output(matter_id=mid, run_id=run.id, kind='relationship', payload={
            'person': 'Alex', 'other_person': 'Morgan', 'statement': 'Relationship remains a draft.',
            'quote': 'Alex attended the meeting.', 'chunk_id': chunk['id']}))
        session.add(main.Output(matter_id=mid, run_id=run.id, kind='event', payload={
            'person': 'Alex', 'date': '2026-01-01', 'statement': 'Dated event.',
            'quote': 'Alex attended the meeting.', 'chunk_id': chunk['id']}))
        session.add(main.Output(matter_id=mid, run_id=run.id, kind='work_product', payload={
            'witness': 'Alex', 'profile': [{'text': 'Alex profile narrative.', 'claim_ids': [finding.id]}],
            'issues': [{'text': 'What remains unresolved?', 'claim_ids': [finding.id], 'category': 'possible_issue'}],
            'coverage': {'warning': 'Selective retrieval only'}, 'draft': True}))
        session.add(main.Output(matter_id=mid, run_id=run.id, kind='questions', payload={
            'settings': {'witness': 'Alex', 'proceeding': 'deposition', 'topics': 'Attendance'},
            'questions': [{'question': f'Saved question {i+1}?', 'purpose': 'Test coverage', 'claim_ids': [finding.id],
                           'review_status': 'approved' if i == 0 else 'needs_review',
                           'wording_history': [{'question': 'Prior original wording?'}]} for i in range(30)]}))
        session.commit()
    return mid


def test_csv_includes_complete_selected_witness_outputs_and_citations(client):
    mid = seed_export(client)
    response = client.post(f'/api/matters/{mid}/witnesses/export', json={'witnesses': ['Alex'], 'format': 'csv'})
    assert response.status_code == 200, response.text
    rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
    assert {r['witness'] for r in rows} == {'Alex'}
    assert {r['section'] for r in rows} == {'Profile Summary', 'Possible Issues', 'Relationships', 'Chronology', 'Supporting Findings', 'Questions', 'Generation And Review Details'}
    questions = [r for r in rows if r['section'] == 'Questions']
    assert len(questions) == 30 and questions[-1]['text'] == 'Saved question 30?'
    assert 'Prior original wording?' in questions[-1]['record_details']
    assert 'source.txt' in questions[0]['citations'] and 'Alex attended the meeting.' in questions[0]['citations']
    assert response.headers['x-export-witnesses'] == '1'


def test_incomplete_validated_question_batches_are_exported_with_labels(client):
    mid=seed_export(client)
    with Session(main.engine) as session:
        run=main.Run(matter_id=mid,provider='ollama',purpose='questions',status='cancelled');session.add(run);session.flush()
        draft=main.Output(matter_id=mid,run_id=run.id,kind='questions_draft',payload={'settings':{'witness':'Alex'},'questions':[{'question':'Saved incomplete question?','purpose':'Follow-up','claim_ids':[],'exploratory':True}],'warning':'Generation incomplete.'})
        session.add(draft);session.commit()
    response=client.post(f'/api/matters/{mid}/witnesses/export',json={'witnesses':['Alex'],'format':'csv'})
    rows=list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
    question=next(row for row in rows if row['text']=='Saved incomplete question?')
    assert 'Incomplete Draft' in question['title']


def test_word_and_zip_preserve_full_questions_and_separate_witnesses(client):
    mid = seed_export(client)
    response = client.post(f'/api/matters/{mid}/witnesses/export', json={'witnesses': ['Alex'], 'format': 'docx'})
    assert response.status_code == 200
    document = Document(io.BytesIO(response.content))
    text = '\n'.join(p.text for p in document.paragraphs)
    assert all(value in text for value in ['Alex profile narrative.', 'What remains unresolved?', 'Saved question 30?', 'Prior original wording?', 'source.txt', 'Needs Review'])
    response = client.post(f'/api/matters/{mid}/witnesses/export', json={'all_witnesses': True, 'format': 'docx_zip'})
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert len(archive.namelist()) == 2
        assert all('/' not in name and name.endswith('.docx') for name in archive.namelist())
        assert any('Morgan' in name for name in archive.namelist())


def test_export_filters_respected_without_hiding_draft_questions_in_metadata(client):
    mid = seed_export(client)
    response = client.post(f'/api/matters/{mid}/witnesses/export', json={'witnesses': ['Alex'], 'sections': ['questions'], 'statuses': ['approved']})
    rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
    assert len(rows) == 1 and rows[0]['text'] == 'Saved question 1?'
    assert 'Saved question 2?' not in response.content.decode('utf-8-sig')
    assert 'Alex profile narrative.' not in response.content.decode('utf-8-sig')


def test_export_is_matter_scoped_and_rejects_stale_or_empty_selection(client):
    mid = seed_export(client)
    other = create(client)
    assert client.post(f'/api/matters/{other}/witnesses/export', json={'witnesses': ['Alex']}).status_code == 400
    assert client.post(f'/api/matters/{mid}/witnesses/export', json={}).status_code == 400
    assert client.post(f'/api/matters/{mid}/witnesses/export', json={'witnesses': ['Foreign']}).status_code == 400
    assert client.post(f'/api/matters/{mid}/witnesses/export', json={'witnesses': ['Alex'], 'sections': []}).status_code == 422
    assert client.post(f'/api/matters/{mid}/delete', json={'confirmation_name': 'Synthetic matter'}).status_code == 200
    assert client.post(f'/api/matters/{mid}/witnesses/export', json={'witnesses': ['Alex']}).status_code == 404


def test_csv_cells_and_word_controls_do_not_execute_or_corrupt_export():
    from app.witness_exports import safe_cell, word_text, slug
    assert safe_cell('  =HYPERLINK("test")').startswith("'")
    assert safe_cell('\t@cmd').startswith("'")
    assert word_text('a\x00b') == 'a\\u0000b'
    assert '/' not in slug('../../escape')


def test_ollama_is_default_for_new_generation_requests():
    from app.full_corpus import FullAnalysisInput
    from app.workproduct import WorkProductInput
    from app.question_context import ContextRevisionInput
    assert main.ExtractionInput().provider == 'ollama'
    assert FullAnalysisInput().provider == 'ollama'
    assert WorkProductInput(witness='Alex').provider == 'ollama'
    assert ContextRevisionInput().provider == 'ollama'


def test_rejected_citation_is_labelled_for_review(client):
    mid = seed_export(client)
    from sqlalchemy import select
    with Session(main.engine) as session:
        claim = session.scalar(select(main.Output).where(main.Output.matter_id == mid, main.Output.kind == 'witness'))
        claim.review_status = 'rejected';session.commit()
    response = client.post(f'/api/matters/{mid}/witnesses/export', json={'witnesses': ['Alex'], 'sections': ['questions'], 'statuses': ['approved']})
    assert response.status_code == 200
    assert 'Claim Review rejected' in response.text and 'Source Requires Review' in response.text
