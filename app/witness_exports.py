"""Matter-scoped exports of saved witness work. No generation, approval or source mutation."""
import csv
import io
import json
import re
import zipfile
from .branding import APP_NAME
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

Section = Literal['profile', 'issues', 'relationships', 'chronology', 'findings', 'questions', 'details']
SECTION_NAMES = {
    'profile': 'Profile Summary', 'issues': 'Possible Issues', 'relationships': 'Relationships',
    'chronology': 'Chronology', 'findings': 'Supporting Findings', 'questions': 'Questions',
    'details': 'Generation And Review Details',
}
STATUSES = ['needs_review', 'approved', 'rejected']


class WitnessExport(BaseModel):
    witnesses: list[str] = Field(default_factory=list, max_length=2000)
    all_witnesses: bool = False
    sections: list[Section] = Field(default_factory=lambda: list(SECTION_NAMES), min_length=1)
    statuses: list[Literal['needs_review', 'approved', 'rejected']] = Field(default_factory=lambda: list(STATUSES), min_length=1)
    format: Literal['csv', 'docx', 'docx_zip'] = 'csv'


def members(output):
    p = output.payload
    if output.kind in {'witness', 'event', 'relationship'}:
        return {n for n in (p.get('person'), p.get('other_person')) if n}
    if output.kind == 'work_product':
        return {p['witness']} if p.get('witness') else set()
    if output.kind in {'questions','questions_draft'}:
        name = p.get('settings', {}).get('witness')
        return {name} if name else set()
    return set()


def export_rows(session, mid, request):
    from . import main as db
    from .witness_profiles import witness_overviews
    matter = db.matter(session, mid)
    outputs = list(session.scalars(select(db.Output).where(db.Output.matter_id == mid).order_by(db.Output.created_at, db.Output.id)))
    names = sorted(set().union(*(members(o) for o in outputs)), key=str.casefold)
    chosen = names if request.all_witnesses else sorted(set(request.witnesses), key=str.casefold)
    if not chosen:
        raise HTTPException(400, 'Select at least one witness to export')
    if not set(chosen) <= set(names):
        raise HTTPException(400, 'A selected witness is unavailable in this workspace. Refresh the witness list.')
    by_id = {o.id: o for o in outputs}
    chunk_ids = {o.payload['chunk_id'] for o in outputs if o.payload.get('chunk_id')}
    sources = {}
    if chunk_ids:
        for chunk, document in session.execute(select(db.Chunk, db.Document).join(db.Document).where(db.Document.matter_id == mid, db.Chunk.id.in_(chunk_ids))):
            sources[chunk.id] = (chunk, document)
    runs = {r.id: r for r in session.scalars(select(db.Run).where(db.Run.matter_id == mid))}
    overview = {w['name']: w for w in witness_overviews(session, mid)['witnesses']} if 'profile' in request.sections else {}
    rows = []

    def citations(ids):
        references = []
        for oid in dict.fromkeys(ids or []):
            claim = by_id.get(oid)
            source = sources.get(claim.payload.get('chunk_id')) if claim else None
            if not source:
                references.append(f'Claim {oid} | Source Unavailable In This Workspace')
                continue
            chunk, document = source
            quote = claim.payload.get('quote', '')
            valid = bool(quote and quote in chunk.text and document.status not in {'failed', 'excluded'} and claim.review_status != 'rejected')
            references.append(f'{document.name} | Document {document.id} | {chunk.locator} | Chunk {chunk.id} | Claim {oid} | Claim Review {claim.review_status} | Source {"Current" if valid else "Requires Review"} | Quote: {quote}')
        return references

    def add(name, section, output, text, *, title='', date='', purpose='', ids=None, status=None, details=None):
        review = status or (output.review_status if output else 'needs_review')
        if section not in request.sections or review not in request.statuses:
            return
        references = citations(ids)
        rows.append({'witness': name, 'section': SECTION_NAMES[section], 'output_id': output.id if output else '',
                     'review_status': review, 'title': title, 'text': text or '', 'date': date or '', 'purpose': purpose or '',
                     'citations': '\n'.join(references), 'citation_entries': references,
                     'record_details': json.dumps(details, ensure_ascii=False, default=str) if details else ''})

    for name in chosen:
        related = [o for o in outputs if name in members(o)]
        products = [o for o in related if o.kind == 'work_product']
        if not any(o.payload.get('profile') for o in products):
            item = overview.get(name, {})
            for paragraph in item.get('summary', []):
                add(name, 'profile', None, paragraph['text'], title='Source Derived Evidence Digest', ids=paragraph.get('claim_ids'),
                    details={'derived_summary': True, 'warnings': item.get('warnings', []), 'limited': item.get('limited')})
        for output in related:
            p = output.payload
            if output.kind in {'witness', 'event', 'relationship'}:
                args = {'ids': [output.id], 'date': p.get('date'), 'details': p}
                add(name, 'findings', output, p.get('statement'), title=output.kind.title(), **args)
                if output.kind == 'relationship':
                    add(name, 'relationships', output, p.get('statement'), title=p.get('other_person') if p.get('person') == name else p.get('person'), **args)
                if output.kind == 'event':
                    add(name, 'chronology', output, p.get('statement'), title='Event' if p.get('date') else 'Undated Event', **args)
            elif output.kind == 'work_product':
                stale = any(e.get('id') not in by_id or by_id[e['id']].revision != e.get('revision') or by_id[e['id']].review_status == 'rejected' for e in p.get('claim_manifest', []))
                for section in ('profile', 'issues'):
                    for i, item in enumerate(p.get(section, []), 1):
                        add(name, section, output, item.get('text'), title=f'{SECTION_NAMES[section]} {i}', ids=item.get('claim_ids'),
                            details={**item, 'source_revisions_changed': stale, 'created_at': str(output.created_at)})
            elif output.kind in {'questions','questions_draft'}:
                for i, question in enumerate(p.get('questions', []), 1):
                    add(name, 'questions', output, question.get('question'), title=f'Question {i}'+(' · Incomplete Draft' if output.kind=='questions_draft' else ''), purpose=question.get('purpose'), ids=question.get('claim_ids'),
                        status=question.get('review_status', 'needs_review'), details={**question, 'outline_settings': p.get('settings', {}), 'outline_created_at': str(output.created_at)})
            # Keep complete per-record generation metadata without leaking sections or
            # question review statuses excluded by the export request.
            if output.kind in {'work_product', 'questions','questions_draft'}:
                metadata = {key: value for key, value in p.items() if key not in {'profile', 'issues', 'questions'}}
                run = runs.get(output.run_id)
                metadata.update({'output_revision': output.revision, 'created_at': str(output.created_at),
                                 'updated_at': str(output.updated_at), 'created_by': output.created_by,
                                 'provider': run.provider if run else '', 'model': run.model if run else '',
                                 'run_id': output.run_id, 'run_status': run.status if run else '', 'run_error': run.error if run else ''})
                add(name, 'details', output, output.kind.replace('_', ' ').title(), details=metadata)
    return matter.name, chosen, rows


def safe_cell(value):
    text = str(value if value is not None else '')
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r')) else text


def csv_bytes(rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=['witness', 'section', 'output_id', 'review_status', 'title', 'text', 'date', 'purpose', 'citations', 'record_details'])
    writer.writeheader()
    writer.writerows({key: safe_cell(row[key]) for key in writer.fieldnames} for row in rows)
    return stream.getvalue().encode('utf-8-sig')


def word_text(value):
    # Preserve otherwise invalid XML controls as visible escapes, rather than dropping evidence.
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', lambda match: f'\\u{ord(match.group()):04x}', str(value))


def docx_bytes(workspace, names, rows, request):
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin = section.bottom_margin = Inches(.75)
    section.left_margin = section.right_margin = Inches(.85)
    normal = document.styles['Normal']
    normal.font.name, normal.font.size = 'Calibri', Pt(11)
    normal.paragraph_format.space_after = Pt(7)
    normal.paragraph_format.line_spacing = 1.12
    for style in document.styles:
        for border in style.element.xpath('.//w:pBdr'):
            border.getparent().remove(border)
    for style in ['Title', 'Heading 1', 'Heading 2', 'Heading 3']:
        document.styles[style].font.color.rgb = RGBColor.from_string('172A3B')
    document.styles['Title'].font.color.rgb = RGBColor(0, 0, 0)
    document.styles['Title'].font.size = Pt(24)
    document.styles['Subtitle'].font.color.rgb = RGBColor.from_string('475569')
    document.add_paragraph('Witness Work Product', 'Title')
    document.add_paragraph(word_text(workspace), 'Subtitle')
    document.add_paragraph(f'Exported {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")} | {len(names)} Witnesses')
    document.add_paragraph('This export contains saved witness outputs and source references for attorney review. Review statuses are recorded per item. Drafts and rejected items are not approved findings; cited statements do not establish legal conclusions.')
    document.add_paragraph('Included Review Statuses: ' + ', '.join(s.replace('_', ' ').title() for s in request.statuses))
    document.add_paragraph('Included Sections: ' + ', '.join(SECTION_NAMES[s] for s in dict.fromkeys(request.sections)))
    for witness_index, name in enumerate(names):
        if witness_index:
            document.add_page_break()
        document.add_heading(word_text(name), 1)
        witness_rows = [r for r in rows if r['witness'] == name]
        source_references = dict.fromkeys(reference for row in witness_rows for reference in row.get('citation_entries', []))
        source_numbers = {reference: index for index, reference in enumerate(source_references, 1)}
        outline_metadata_seen = set()
        if not witness_rows:
            document.add_paragraph('No saved items match the selected sections and review statuses.')
        for section_name in SECTION_NAMES.values():
            items = [r for r in witness_rows if r['section'] == section_name]
            if section_name == SECTION_NAMES['chronology']:
                items.sort(key=lambda row: row['date'] or '9999')
            if not items:
                continue
            document.add_heading(section_name, 2)
            for row in items:
                details = json.loads(row['record_details']) if row['record_details'] else {}
                if row['output_id'] not in outline_metadata_seen and details.get('outline_settings'):
                    outline_metadata_seen.add(row['output_id'])
                    document.add_paragraph(word_text('Outline Settings: ' + json.dumps(details['outline_settings'], ensure_ascii=False)))
                    document.add_paragraph(word_text('Outline Created: ' + details.get('outline_created_at', '')))
                meta = ' | '.join(filter(None, [row['title'], row['review_status'].replace('_', ' ').title(), row['date']]))
                paragraph = document.add_paragraph()
                paragraph.paragraph_format.keep_with_next = True
                paragraph.add_run(word_text(meta)).bold = True
                document.add_paragraph(word_text(row['text']))
                if row['purpose']:
                    document.add_paragraph(word_text('Purpose: ' + row['purpose']))
                if row['citations']:
                    references = row.get('citation_entries', [])
                    labels = ', '.join(f'S{source_numbers[reference]}' for reference in references)
                    paragraph = document.add_paragraph(word_text('Sources: ' + (labels or row['citations'])))
                    for run in paragraph.runs:
                        run.font.size = Pt(9)
                if row['record_details']:
                    # Include original wording/history, settings, limits, coverage and
                    # other saved metadata as readable indented lines, not a clipped table.
                    rendered_details = []
                    for key, value in details.items():
                        if key in {'text', 'question', 'purpose', 'claim_ids', 'quote', 'statement', 'review_status', 'outline_settings', 'outline_created_at'}:
                            continue
                        value = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
                        rendered_details.append(key.replace('_', ' ').title() + ': ' + value)
                    if rendered_details:
                        paragraph = document.add_paragraph(word_text(' | '.join(rendered_details)))
                        for run in paragraph.runs:
                            run.font.size = Pt(9)
        if source_references:
            document.add_heading('Source References', 2)
            for reference, number in source_numbers.items():
                paragraph = document.add_paragraph()
                paragraph.paragraph_format.keep_with_next = True
                paragraph.add_run(f'S{number}').bold = True
                paragraph = document.add_paragraph(word_text(reference))
                for run in paragraph.runs:
                    run.font.size = Pt(9)
    footer = section.footer.paragraphs[0]
    footer.add_run(APP_NAME + ' | Page ')
    page = OxmlElement('w:fldSimple')
    page.set(qn('w:instr'), 'PAGE')
    footer._p.append(page)
    document.core_properties.title = 'Witness Work Product'
    document.core_properties.author = APP_NAME
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def slug(value):
    return re.sub(r'[^A-Za-z0-9_-]+', '-', value).strip('-')[:65] or 'witness'


def register_witness_exports(app):
    from . import main as db

    @app.post('/api/matters/{mid}/witnesses/export')
    def export(mid: str, request: WitnessExport):
        with Session(db.engine) as session:
            if db.engine.dialect.name == 'postgresql':
                session.connection(execution_options={'isolation_level': 'REPEATABLE READ'})
            workspace, names, rows = export_rows(session, mid, request)
        if request.format == 'csv':
            data, media, extension = csv_bytes(rows), 'text/csv; charset=utf-8', 'csv'
        elif request.format == 'docx':
            data, media, extension = docx_bytes(workspace, names, rows, request), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'docx'
        else:
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
                for index, name in enumerate(names, 1):
                    archive.writestr(f'{index:04d}-{slug(name)}.docx', docx_bytes(workspace, [name], rows, request))
            data, media, extension = stream.getvalue(), 'application/zip', 'zip'
        return Response(data, media_type=media, headers={'Content-Disposition': f'attachment; filename="{slug(workspace)}-witnesses.{extension}"', 'X-Export-Witnesses': str(len(names)), 'X-Export-Items': str(len(rows))})
