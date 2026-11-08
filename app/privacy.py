"""Deterministic pre-egress filtering; not a legal de-identification guarantee."""
import hashlib
import hmac
import re

PATTERNS = {
    'EMAIL': r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b',
    'SSN': r'\b\d{3}-\d{2}-\d{4}\b',
    'PHONE': r'(?<!\w)(?:\+1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b',
    'CARD': r'\b(?:\d[ -]?){13,19}\b',
    'MEDICAL_ID': r'(?i)\b(?:MRN|medical record|patient id)\s*[:#]?\s*[A-Z0-9-]+',
    'DOB': r'(?i)\b(?:DOB|date of birth)\s*[:#]?\s*\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}',
}

def sanitize(text: str, matter: str, key: str, terms: list[str]):
    spans = []
    for kind, pattern in PATTERNS.items():
        spans.extend((m.start(), m.end(), kind) for m in re.finditer(pattern, text))
    for term in terms:
        if term.strip():
            spans.extend((m.start(), m.end(), 'CUSTOM') for m in re.finditer(re.escape(term), text, re.I))
    selected = []
    for start, end, kind in sorted(spans, key=lambda s: (s[0], -s[1])):
        if selected and start < selected[-1][1]:
            continue
        selected.append((start, end, kind))
    result, cursor = [], 0
    for start, end, kind in selected:
        digest = hmac.new(key.encode(), (matter + '\0' + text[start:end].casefold()).encode(), hashlib.sha256).hexdigest()[:16]
        result.extend([text[cursor:start], f'[{kind}_{digest}]'])
        cursor = end
    result.append(text[cursor:])
    return ''.join(result), [{'start': s, 'end': e, 'kind': k} for s, e, k in selected]
