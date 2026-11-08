"""Cooperative controls, durable requests, and metadata-only model measurements."""
import asyncio
import threading
import time
from contextvars import ContextVar

from sqlalchemy import select
from sqlalchemy.orm import Session

current_job = ContextVar('current_job', default=None)
measurement = ContextVar('model_measurement', default=None)
_active = {}
_lock = threading.RLock()


def bind(db, run_id, mid, request=None, parent=None):
    ancestor = current_job.get()
    context = {'db': db, 'id': run_id, 'mid': mid, 'parent': ancestor, 'cancel': False, 'pause': False,
               'phase': 'Preparing', 'call_started': None, 'external_parent':parent}
    if request is not None:
        with Session(db.engine) as session:
            db.audit(session, mid, 'job_request_saved', {'run_id': run_id, 'request': request, 'parent_run_id': ancestor['id'] if ancestor else parent})
            session.commit()
    with _lock:
        _active[run_id] = context
    return current_job.set(context)


def unbind(token):
    context = current_job.get()
    if context:
        with _lock:
            _active.pop(context['id'], None)
    current_job.reset(token)


def live(run_id):
    with _lock:
        context = _active.get(run_id)
        if not context:
            return None
        return {'pause_requested': context['pause'], 'cancel_requested': context['cancel'],
                'parent_run_id': context['parent']['id'] if context['parent'] else context.get('external_parent'),
                'phase': context['phase'], 'model_call_seconds': time.monotonic() - context['call_started'] if context['call_started'] else None}


def control(run_id, action):
    with _lock:
        context = _active.get(run_id)
        if not context:
            return False
        if action == 'cancel':
            context['cancel'] = True
            context['pause'] = False
        elif action == 'pause':
            context['pause'] = True
        elif action == 'resume':
            context['pause'] = False
        return True


def cancelled():
    context = current_job.get()
    while context:
        if context['cancel']:
            return True
        context = context['parent']
    return False


async def boundary():
    from .providers import ProviderError
    while True:
        if cancelled():
            raise ProviderError('Cancelled by the operator. Validated checkpoints and earlier reviewed outputs are retained.', 'CANCELLED', 409)
        context = current_job.get()
        paused = False
        while context:
            paused = paused or context['pause']
            context = context['parent']
        if not paused:
            return
        await asyncio.sleep(.25)


def begin_measurement(provider, model):
    context = current_job.get()
    data = {'provider': provider, 'model': model, 'started': time.monotonic(), 'attempts': 0,
            'input_tokens': None, 'output_tokens': None, 'generation_seconds': None, 'load_seconds': None}
    if context:
        context['call_started'] = data['started']
        context['phase'] = 'Waiting For Model'
    return measurement.set(data)


def request_attempt():
    if measurement.get() is not None:
        measurement.get()['attempts'] += 1


def capture_usage(provider, data):
    target = measurement.get()
    if target is None or not isinstance(data,dict):
        return
    usage = data.get('usage') or data.get('usageMetadata') or {}
    if not isinstance(usage,dict):return
    fields = {'input_tokens': usage.get('input_tokens', usage.get('promptTokenCount', data.get('prompt_eval_count'))),
              'output_tokens': usage.get('output_tokens', usage.get('candidatesTokenCount', data.get('eval_count'))),
              'cached_input_tokens': (usage.get('input_tokens_details') or {}).get('cached_tokens', usage.get('cache_read_input_tokens'))}
    for key, value in fields.items():
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            target[key] = (target.get(key) or 0) + value
    for key, source in [('generation_seconds', 'eval_duration'), ('load_seconds', 'load_duration')]:
        value = data.get(source)
        if isinstance(value, (int, float)) and value >= 0:
            target[key] = (target.get(key) or 0) + value / 1e9


def finish_measurement(token, error=None):
    data, context = measurement.get(), current_job.get()
    try:
        if data and context:
            details = {key: value for key, value in data.items() if key != 'started'}
            details.update(run_id=context['id'], duration_seconds=time.monotonic() - data['started'],
                           status='failed' if error else 'completed', error_code=getattr(error, 'code', 'UNEXPECTED_ERROR') if error else None)
            with Session(context['db'].engine) as session:
                context['db'].audit(session, context['mid'], 'model_call_measured', details)
                session.commit()
    except Exception:
        # Measurements must never change evidence acceptance or make a successful call fail.
        pass
    finally:
        if context:
            context['call_started'] = None
            context['phase'] = 'Validating Or Saving'
        measurement.reset(token)


def reconcile(db):
    """Called at server startup, before accepting requests; full jobs have their own recovery."""
    with Session(db.engine) as session:
        for run in session.scalars(select(db.Run).where(db.Run.status == 'running', db.Run.purpose != 'full_corpus')):
            run.status = 'interrupted'
            run.error = 'Server restarted before this request completed. Saved checkpoints are retained. Retry the saved request explicitly.'
            db.audit(session, run.matter_id, 'job_interrupted', {'run_id': run.id})
        session.commit()
