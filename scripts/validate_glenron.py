"""Offline source-integrity checks. Does not call an AI provider or import the answer key."""
from pathlib import Path
import hashlib
import json
from app.extract import extract

def main():
    root=Path(__file__).resolve().parents[1]/'data'/'glenron'
    benchmark=root/'benchmark_DO_NOT_IMPORT'
    manifest=json.loads((benchmark/'manifest.json').read_text())
    key=json.loads((benchmark/'answer_key.json').read_text())
    assert len(list((root/'documents').iterdir()))==500
    texts={};passages=0
    for record in manifest['files']:
        path=root/'documents'/record['file']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==record['sha256']
        parts=list(extract(path));assert parts and all(text.strip() for _,text in parts)
        text=' '.join(' '.join(text.split()) for _,text in parts)
        assert record['document_id'] in text and 'SYNTHETIC TEST EVIDENCE' in text
        texts[record['document_id']]=text;passages+=len(parts)
    for event in key['events']:
        assert event['assertion'] in texts[event['primary_anchor']],event['event_id']
        assert event['variance']==event['booked_value']-event['comparison_value']
    assert all(any(person['name'] in text for text in texts.values()) for person in key['people'])
    assert 'GLN-999999' not in texts
    assert all(source in texts for relation in key['relationships'] for source in relation['sources'])
    assert sum(e['variance'] for e in key['events'])==key['financial_check']['comparison_variance_total_usd']
    report={'documents_validated':len(texts),'extracted_passages':passages,'primary_event_anchors_verified':len(key['events']),
        'people_covered':len(key['people']),'directed_email_relationships':len(key['relationships']),
        'all_hashes_verified':True,'answer_key_excluded_from_evidence':True,'actual_ai_calls':0}
    (benchmark/'validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
