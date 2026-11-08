"""Conservative witness digests and explicit evidence/chronology limits."""
import re
from collections import defaultdict
from sqlalchemy import select
from sqlalchemy.orm import Session

GENERIC_REVIEW=re.compile(r'^(?:I|we)\s+(?:have\s+)?reviewed\b',re.I)
DATE_WORDS=re.compile(r'\b\d{4}\b|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b',re.I)

def supported_date(value):
    return bool(value and value.strip().lower() not in {'unknown','undated','n/a','none','not known'} and DATE_WORDS.search(value))

def assess_witness(name,findings,document_ids,coverage=None):
    """Counts describe saved findings, never statistical confidence or completeness."""
    items=[o for o in findings if o['review_status']!='rejected' and name in [o['payload'].get('person'),o['payload'].get('other_person')]]
    unique={re.sub(r'\s+',' ',o['payload'].get('statement','').strip()).casefold() for o in items if o['payload'].get('statement','').strip()}
    useful={re.sub(r'\s+',' ',o['payload'].get('statement','').strip()).casefold() for o in items if o['payload'].get('statement','').strip() and not GENERIC_REVIEW.match(o['payload']['statement'].strip())}
    docs={document_ids[o['id']] for o in items if o['id'] in document_ids}
    dated=[o for o in items if o['kind']=='event' and supported_date(o['payload'].get('date',''))]
    undated=[o for o in items if o['kind']=='event' and not supported_date(o['payload'].get('date',''))]
    roles=defaultdict(list)
    for o in items:
        if o['kind']!='witness' or o['payload'].get('person')!=name:continue
        statement=o['payload'].get('statement','').strip()
        match=re.fullmatch(re.escape(name)+r'\s+(?:is|was|serves as|served as)\s+([^.!?\n]{3,120})[.!]?\s*',statement,re.I)
        if not match:continue
        role=match[1].strip(' .')
        quote=o['payload'].get('quote','')
        if name.casefold() not in quote.casefold() or role.casefold() not in quote.casefold():continue
        key=next((key for key in roles if key.casefold()==role.casefold()),role);roles[key].append(o['id'])
    summary=[]
    for role,ids in list(roles.items())[:2]:summary.append({'text':f'Available findings describe {name} as {role}.','claim_ids':ids})
    if not summary:summary=[{'text':f'The current findings identify {name}, but do not yet establish a clear role or enough context to describe their involvement.','claim_ids':[]}]
    limited=len(useful)<4 or len(docs)<3
    warnings=[]
    if limited:
        finding_word='finding' if len(items)==1 else 'findings';document_word='source document' if len(docs)==1 else 'source documents';point_word='profile point' if len(useful)==1 else 'profile points';verb='provides' if len(items)==1 else 'provide'
        warnings.append(f'Limited evidence: {len(items)} {finding_word} across {len(docs)} {document_word} {verb} only {len(useful)} distinct {point_word}. There is not enough supported information for a fuller profile. Responsibilities, involvement and relationships may remain unclear.')
    if len(roles)>1:warnings.append('Different role descriptions appear in the evidence. Review their scope and dates; they have not been treated as an identity match or a proven contradiction.')
    if coverage and coverage['analyzed_passages']<coverage['total_passages']:warnings.append(f"Witness extraction has checkpointed {coverage['analyzed_passages']} of {coverage['total_passages']} current passages. More relevant evidence may remain unanalyzed; indexing is a separate step.")
    chronology_notice=(f'No supported dated events have been identified for {name} in the current findings. A reliable dated chronology cannot yet be built. Message dates are not automatically treated as event dates.' if not dated else f'{len(dated)} source-linked dated events are available. This chronology covers current findings and may be incomplete.')
    return {'name':name,'summary':summary,'limited':limited,'warnings':warnings,'finding_count':len(items),'unique_findings':len(unique),'distinct_profile_points':len(useful),'source_documents':len(docs),'dated_event_ids':[o['id'] for o in sorted(dated,key=lambda o:o['payload']['date'])],'undated_event_count':len(undated),'chronology_notice':chronology_notice}


def witness_overviews(s,mid):
    from . import main as db
    db.matter(s,mid)
    rows=list(s.scalars(select(db.Output).where(db.Output.matter_id==mid,db.Output.kind.in_(['witness','event','relationship']),db.Output.review_status!='rejected')))
    chunk_ids={o.payload.get('chunk_id') for o in rows if o.payload.get('chunk_id')}
    chunks={c.id:c for c in s.scalars(select(db.Chunk).join(db.Document).where(db.Document.matter_id==mid,db.Document.status.notin_(['excluded','failed']),db.Chunk.id.in_(chunk_ids)))}
    valid=[o for o in rows if o.payload.get('chunk_id') in chunks and o.payload.get('quote') and o.payload['quote'] in chunks[o.payload['chunk_id']].text]
    document_ids={o.id:chunks[o.payload['chunk_id']].document_id for o in valid}
    current=dict(s.execute(select(db.Chunk.id,db.Chunk.revision).join(db.Document).where(db.Document.matter_id==mid,db.Document.status.notin_(['excluded','failed']))).all())
    processed=set()
    for run in s.scalars(select(db.Run).where(db.Run.matter_id==mid,db.Run.purpose=='extraction')):
        processed.update(e['id'] for e in run.manifest if e.get('processed') and current.get(e.get('id'))==e.get('revision'))
    coverage={'total_passages':len(current),'analyzed_passages':len(processed)}
    findings=[db.pack(o) for o in valid]
    names=sorted({name for o in findings for name in [o['payload'].get('person'),o['payload'].get('other_person')] if name})
    result=[assess_witness(name,findings,document_ids,coverage) for name in names]
    for item in result:
        related=[name for name in names if name!=item['name'] and len(name.split())>1 and name.split()[0].casefold()==item['name'].split()[0].casefold()]
        item['similar_names']=related
    return {'witnesses':result,'coverage':coverage}


def register_witness_overviews(app):
    from . import main as db
    @app.get('/api/matters/{mid}/witnesses/overview')
    def overviews(mid:str):
        with Session(db.engine) as s:return witness_overviews(s,mid)
