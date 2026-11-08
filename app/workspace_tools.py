"""Attorney review, identity decisions, case organization and saved workspace views."""
import copy
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session


class IdentityDecision(BaseModel):
    alias: str = Field(min_length=1, max_length=200)
    canonical: str = Field(min_length=1, max_length=200)
    confirmed: bool = False


class CaseIssue(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    question: str = Field(default='', max_length=5000)
    status: Literal['open', 'disputed', 'resolved'] = 'open'
    supporting_claim_ids: list[str] = Field(default_factory=list, max_length=200)
    contradicting_claim_ids: list[str] = Field(default_factory=list, max_length=200)
    witness_names: list[str] = Field(default_factory=list, max_length=100)
    revision: int | None = None


class SavedView(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    section: Literal['Import', 'Witnesses', 'Review', 'Case']
    values: dict[str, str] = Field(default_factory=dict)


def replace_identity(value, alias, canonical):
    if isinstance(value, dict):
        return {key: canonical if key in {'person', 'other_person', 'witness'} and isinstance(item, str) and item.casefold() == alias.casefold()
                else replace_identity(item, alias, canonical) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_identity(item, alias, canonical) for item in value]
    return value


def register_workspace_tools(app):
    from . import main as db

    def idle(session, mid):
        db.matter(session, mid)
        session.execute(select(db.Matter).where(db.Matter.id == mid).with_for_update()).scalar_one()
        if session.scalar(select(db.Run.id).where(db.Run.matter_id == mid, db.Run.status.in_(['running', 'paused'])).limit(1)):
            raise HTTPException(409, 'Finish or cancel the active job before changing witness identities.')

    @app.get('/api/matters/{mid}/attention')
    def attention(mid: str):
        with Session(db.engine) as session:
            db.matter(session, mid)
            runs = list(session.scalars(select(db.Run).where(db.Run.matter_id == mid).order_by(db.Run.created_at.desc()).limit(100)))
            outputs = list(session.scalars(select(db.Output).where(db.Output.matter_id == mid)))
            pending = sum(o.review_status == 'needs_review' for o in outputs if o.kind in {'witness', 'event', 'relationship', 'work_product'})
            questions = sum(q.get('review_status', 'needs_review') == 'needs_review' for o in outputs if o.kind in {'questions', 'questions_draft'} for q in o.payload.get('questions', []))
            addressed = {event.details.get('run_id') for event in session.scalars(select(db.Audit).where(db.Audit.matter_id == mid, db.Audit.action == 'job_attention_addressed'))}
            failed = [db.pack(run) for run in runs if run.status in {'failed', 'partial', 'interrupted'} and run.id not in addressed]
            return {'pending_findings_and_products': pending, 'pending_questions': questions, 'jobs_needing_attention': failed[:20], 'active_jobs': sum(run.status in {'running', 'paused'} for run in runs)}

    @app.post('/api/matters/{mid}/attention/jobs/{rid}/addressed')
    def addressed(mid: str, rid: str):
        with Session(db.engine) as session:
            db.matter(session, mid)
            run = session.get(db.Run, rid)
            if not run or run.matter_id != mid:
                raise HTTPException(404, 'Job not found')
            if run.status not in {'failed', 'partial', 'interrupted'}:
                raise HTTPException(409, 'Only stopped jobs can be marked as addressed.')
            db.audit(session, mid, 'job_attention_addressed', {'run_id': rid}); session.commit()
        return {'message': 'Removed from attention reminders. Job status and history are unchanged.'}

    @app.get('/api/matters/{mid}/search')
    def search(mid: str, q: str = ''):
        q = q.strip()
        if not 2 <= len(q) <= 200:
            raise HTTPException(400, 'Enter between 2 and 200 characters.')
        results = []
        with Session(db.engine) as session:
            db.matter(session, mid)
            needle = q.casefold()
            # Literal matching avoids SQL wildcard surprises and never searches other matters.
            for document in session.scalars(select(db.Document).where(db.Document.matter_id == mid)):
                if needle in document.name.casefold():
                    results.append({'kind': 'Document', 'title': document.name, 'document_id': document.id})
            pattern = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            for chunk, document in session.execute(select(db.Chunk, db.Document).join(db.Document).where(db.Document.matter_id == mid, db.Chunk.text.ilike(pattern, escape='\\')).limit(50)):
                position = chunk.text.casefold().find(needle)
                results.append({'kind': 'Passage', 'title': document.name + ' · ' + chunk.locator, 'document_id': document.id,
                                'chunk_id': chunk.id, 'excerpt': chunk.text[max(0, position - 100):position + 250]})
            for output in session.scalars(select(db.Output).where(db.Output.matter_id == mid)):
                if len(results) >= 100:
                    break
                if output.kind in {'questions', 'questions_draft'}:
                    for index, question in enumerate(output.payload.get('questions', [])):
                        if needle in question.get('question', '').casefold():
                            results.append({'kind': 'Question', 'title': question['question'], 'output_id': output.id, 'index': index})
                            if len(results) >= 100:
                                break
                elif needle in json.dumps(output.payload, ensure_ascii=False).casefold():
                    results.append({'kind': output.kind, 'title': output.payload.get('statement') or output.payload.get('title') or output.payload.get('witness') or output.kind,
                                    'output_id': output.id, 'chunk_id': output.payload.get('chunk_id')})
        return {'results': results[:100], 'limit': 100}

    @app.get('/api/matters/{mid}/review-queue')
    def review_queue(mid: str, status: str = 'needs_review', kind: str = '', q: str = '', offset: int = 0):
        if status not in {'needs_review', 'approved', 'rejected', 'all'} or offset < 0 or len(q) > 200:
            raise HTTPException(400, 'Invalid review filter')
        items = []
        with Session(db.engine) as session:
            db.matter(session, mid)
            for output in session.scalars(select(db.Output).where(db.Output.matter_id == mid).order_by(db.Output.created_at, db.Output.id)):
                payload = output.payload
                if output.kind in {'questions', 'questions_draft'}:
                    for index, question in enumerate(payload.get('questions', [])):
                        items.append({'id': output.id, 'index': index, 'revision': output.revision, 'kind': 'question', 'status': question.get('review_status', 'needs_review'),
                                      'witness': payload.get('settings', {}).get('witness', ''), 'text': question.get('question', ''),
                                      'claim_ids': question.get('claim_ids', []), 'partial': output.kind == 'questions_draft'})
                elif output.kind in {'witness', 'event', 'relationship', 'work_product'}:
                    items.append({'id': output.id, 'revision': output.revision, 'kind': output.kind, 'status': output.review_status,
                                  'witness': payload.get('person', payload.get('witness', '')), 'text': payload.get('statement') or '\n'.join(x.get('text', '') for x in payload.get('profile', [])) or 'Draft work product',
                                  'claim_ids': [output.id] if output.kind != 'work_product' else [], 'chunk_id': payload.get('chunk_id')})
            filtered = [item for item in items if (status == 'all' or item['status'] == status) and (not kind or item['kind'] == kind) and (not q or q.casefold() in (item['text'] + ' ' + item['witness']).casefold())]
            return {'items': filtered[offset:offset + 25], 'total': len(filtered), 'offset': offset}

    @app.get('/api/matters/{mid}/identities')
    def identities(mid: str):
        with Session(db.engine) as session:
            db.matter(session, mid)
            names = sorted({name for output in session.scalars(select(db.Output).where(db.Output.matter_id == mid, db.Output.kind.in_(['witness', 'event', 'relationship']))) for name in (output.payload.get('person'), output.payload.get('other_person')) if name})
            audits = list(session.scalars(select(db.Audit).where(db.Audit.matter_id == mid, db.Audit.action.in_(['identity_merged', 'identity_merge_undone'])).order_by(db.Audit.created_at)))
            undone = {a.details.get('merge_id') for a in audits if a.action == 'identity_merge_undone'}
            decisions = [{'id': a.id, 'alias': a.details['alias'], 'canonical': a.details['canonical'], 'undone': a.id in undone, 'created_at': a.created_at.isoformat()} for a in audits if a.action == 'identity_merged']
            return {'names': names, 'decisions': decisions}

    @app.post('/api/matters/{mid}/identities/merge')
    def merge(mid: str, request: IdentityDecision):
        if not request.confirmed or request.alias.casefold() == request.canonical.casefold():
            raise HTTPException(400, 'Confirm two distinct identities after reviewing their source evidence.')
        with Session(db.engine) as session:
            idle(session, mid)
            outputs = list(session.scalars(select(db.Output).where(db.Output.matter_id == mid).with_for_update()))
            names = {name for output in outputs for name in (output.payload.get('person'), output.payload.get('other_person'), output.payload.get('witness'), output.payload.get('settings', {}).get('witness')) if name}
            if request.alias not in names or request.canonical not in names:
                raise HTTPException(400, 'Both identities must exist in this workspace.')
            snapshots = []
            for output in outputs:
                changed = replace_identity(output.payload, request.alias, request.canonical)
                if changed != output.payload:
                    snapshots.append({'id': output.id, 'before': copy.deepcopy(output.payload), 'after': changed, 'revision_after': output.revision + 1})
                    output.payload = changed
                    output.revision += 1
            audit = db.Audit(matter_id=mid, action='identity_merged', details={'alias': request.alias, 'canonical': request.canonical, 'snapshots': snapshots})
            session.add(audit); session.commit()
            return {'merge_id': audit.id, 'changed_outputs': len(snapshots), 'message': 'Identity fields merged. Original quotations and narrative wording remain unchanged.'}

    @app.post('/api/matters/{mid}/identities/{merge_id}/undo')
    def undo(mid: str, merge_id: str):
        with Session(db.engine) as session:
            idle(session, mid)
            audit = session.get(db.Audit, merge_id)
            if not audit or audit.matter_id != mid or audit.action != 'identity_merged':
                raise HTTPException(404, 'Identity decision not found')
            if any(a.details.get('merge_id') == merge_id for a in session.scalars(select(db.Audit).where(db.Audit.matter_id == mid, db.Audit.action == 'identity_merge_undone'))):
                raise HTTPException(409, 'This merge has already been undone.')
            outputs = []
            for snapshot in audit.details['snapshots']:
                output = session.scalar(select(db.Output).where(db.Output.id == snapshot['id'], db.Output.matter_id == mid).with_for_update())
                if not output or output.payload != snapshot['after']:
                    raise HTTPException(409, 'Saved work changed after this merge. No records were overwritten; review the identity decision manually.')
                outputs.append((output, snapshot))
            for output, snapshot in outputs:
                output.payload = snapshot['before']; output.revision += 1
            db.audit(session, mid, 'identity_merge_undone', {'merge_id': merge_id}); session.commit()
            return {'restored_outputs': len(outputs)}

    @app.get('/api/matters/{mid}/case')
    def case(mid: str):
        with Session(db.engine) as session:
            db.matter(session, mid)
            issues = list(session.scalars(select(db.Output).where(db.Output.matter_id == mid, db.Output.kind == 'case_issue')))
            events = list(session.scalars(select(db.Output).where(db.Output.matter_id == mid, db.Output.kind == 'event', db.Output.review_status != 'rejected')))
            from .witness_profiles import supported_date
            dated = sorted([event for event in events if supported_date(event.payload.get('date', ''))], key=lambda event: event.payload['date'])
            return {'issues': [db.pack(issue) for issue in issues], 'events': [db.pack(event) for event in dated], 'undated_events': len(events) - len(dated)}

    def save_issue(session, mid, request, output=None):
        db.matter(session, mid)
        for cid in {*request.supporting_claim_ids, *request.contradicting_claim_ids}:
            claim = session.get(db.Output, cid)
            if not claim or claim.matter_id != mid or claim.kind not in {'witness', 'event', 'relationship'}:
                raise HTTPException(400, 'Every linked finding must belong to this workspace.')
        if output:
            if request.revision != output.revision:
                raise HTTPException(409, 'Issue changed; refresh before saving.')
            before = copy.deepcopy(output.payload); output.payload = request.model_dump(exclude={'revision'}); output.revision += 1
        else:
            before = None
            run = db.Run(matter_id=mid, provider='local-operator', model='', purpose='case_organization', status='completed', manifest=[])
            session.add(run); session.flush()
            output = db.Output(matter_id=mid, run_id=run.id, kind='case_issue', payload=request.model_dump(exclude={'revision'}))
            session.add(output); session.flush()
        db.audit(session, mid, 'case_issue_saved', {'output_id': output.id, 'before': before, 'after': output.payload}); session.commit()
        return db.pack(output)

    @app.post('/api/matters/{mid}/case/issues')
    def add_issue(mid: str, request: CaseIssue):
        with Session(db.engine) as session:
            return save_issue(session, mid, request)

    @app.post('/api/matters/{mid}/case/issues/{oid}')
    def edit_issue(mid: str, oid: str, request: CaseIssue):
        with Session(db.engine) as session:
            output = session.scalar(select(db.Output).where(db.Output.id == oid, db.Output.matter_id == mid, db.Output.kind == 'case_issue').with_for_update())
            if not output:
                raise HTTPException(404, 'Issue not found')
            return save_issue(session, mid, request, output)

    @app.get('/api/matters/{mid}/views')
    def views(mid: str):
        with Session(db.engine) as session:
            db.matter(session, mid)
            latest = {}
            for event in session.scalars(select(db.Audit).where(db.Audit.matter_id == mid, db.Audit.action == 'view_saved').order_by(db.Audit.created_at)):
                latest[(event.details['section'], event.details['name'])] = event.details
            return list(latest.values())

    @app.post('/api/matters/{mid}/views')
    def save_view(mid: str, request: SavedView):
        if len(request.values) > 12 or any(len(value) > 500 for value in request.values.values()):
            raise HTTPException(400, 'Saved view is too large')
        with Session(db.engine) as session:
            db.matter(session, mid); db.audit(session, mid, 'view_saved', request.model_dump()); session.commit()
        return {'saved': True}
