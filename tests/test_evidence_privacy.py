from app.privacy import sanitize
from app.evidence import Finding, validate_finding

def test_stable_matter_scoped_masking():
    text = 'Contact jane@example.com. MRN: 123456 and 123-45-6789.'
    one, hits = sanitize(text, 'matter-a', 'a-long-random-test-key', [])
    two, _ = sanitize(text, 'matter-a', 'a-long-random-test-key', [])
    other, _ = sanitize(text, 'matter-b', 'a-long-random-test-key', [])
    assert one == two and one != other
    assert all(value not in one for value in ['jane@example.com', '123456', '123-45-6789'])
    assert len(hits) == 3

def test_custom_terms_case_insensitive_and_overlapping():
    result, hits = sanitize('Jane Doe met Jane.', 'm', 'key', ['Jane', 'Jane Doe'])
    assert 'Jane' not in result and len(hits) == 2

def test_fabricated_or_foreign_quote_rejected():
    f = Finding(kind='event', person='Alex', statement='Alex attended.', chunk_id='a', quote='Alex attended the meeting.')
    assert validate_finding(f, {'a': 'Yesterday Alex attended the meeting.'})
    assert not validate_finding(f, {'b': 'Alex attended the meeting.'})
    assert not validate_finding(f, {'a': 'Alex did not attend the meeting.'})
