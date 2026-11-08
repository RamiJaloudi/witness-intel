"""Local operator telemetry. Never collect command lines, environments or prompts."""
import csv
import io
import os
import secrets
import shutil
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
import psutil
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session


def number(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def gpu_snapshot():
    executable = shutil.which('nvidia-smi')
    if not executable:
        return {'devices': [], 'processes': [], 'message': 'NVIDIA telemetry unavailable. AMD and Intel monitoring is not supported.'}
    def query(fields, kind='gpu'):
        result = subprocess.run([executable, f'--query-{kind}={fields}', '--format=csv,noheader,nounits'],
                                capture_output=True, text=True, timeout=3, check=True,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return list(csv.reader(io.StringIO(result.stdout), skipinitialspace=True))
    try:
        fields = ['index', 'uuid', 'name', 'driver_version', 'utilization.gpu', 'memory.used', 'memory.total', 'temperature.gpu', 'power.draw', 'power.limit']
        devices = []
        for row in query(','.join(fields)):
            if len(row) != len(fields):
                continue
            device = dict(zip(fields, row))
            devices.append({key: value if key in {'uuid', 'name', 'driver_version'} else number(value) for key, value in device.items()})
        processes = []
        message = 'Compute processes only. Windows may not report per-process GPU memory.'
        try:
            for row in query('gpu_uuid,pid,used_memory', 'compute-apps'):
                if len(row) == 3 and number(row[1]) is not None:
                    processes.append({'gpu_uuid': row[0], 'pid': int(row[1]), 'memory_mb': number(row[2])})
        except (OSError, subprocess.SubprocessError):
            message = 'GPU metrics available; compute process information unavailable.'
        return {'devices': devices, 'processes': processes, 'message': message}
    except (OSError, subprocess.SubprocessError):
        return {'devices': [], 'processes': [], 'message': 'GPU driver did not respond. System and job monitoring remain available.'}


class Estimator:
    def __init__(self):
        self.samples = {}
        self.lock = threading.Lock()

    def observe(self, key, phase, done, total, status, clock=None):
        clock = time.monotonic() if clock is None else clock
        result = {'rate_per_minute': None, 'eta_seconds': None, 'eta_range_seconds': None,
                  'estimate_note': 'Collecting recent progress; estimates cover this stage only.'}
        with self.lock:
            previous = self.samples.get(key)
            if not previous or previous[0] != (phase, status) or status != 'running' or done < previous[1][-1][1]:
                previous = ((phase, status), deque(maxlen=12))
                self.samples[key] = previous
            samples = previous[1]
            if not samples or done != samples[-1][1]:
                samples.append((clock, done))
            if len(self.samples) > 1000:
                self.samples.pop(next(iter(self.samples)))
            if status != 'running':
                result['estimate_note'] = 'No estimate while paused or finished.'
            elif samples and clock - samples[-1][0] > 90:
                result['estimate_note'] = 'No recent completed units. A model request may still be working; ETA unavailable.'
            elif len(samples) >= 3 and samples[-1][0] - samples[0][0] >= 10 and total > done:
                rate = (samples[-1][1] - samples[0][1]) / (samples[-1][0] - samples[0][0])
                if rate > 0:
                    eta = (total - done) / rate
                    result.update(rate_per_minute=rate * 60, eta_seconds=eta,
                                  eta_range_seconds=[eta * .5, eta * 2],
                                  estimate_note='Low confidence: recent pace; approximate range, not a statistical guarantee. Later stages excluded.')
        return result


def protection(process):
    """Conservative termination policy, checked again immediately before signalling."""
    try:
        own = psutil.Process()
        protected = {own.pid, *[p.pid for p in own.parents()]}
        name = process.name().lower()
        if process.pid <= 4 or process.pid in protected:
            return 'Application and parent/system processes are protected.'
        if name in {'system', 'registry', 'smss.exe', 'csrss.exe', 'wininit.exe', 'services.exe', 'lsass.exe', 'svchost.exe', 'winlogon.exe', 'dwm.exe', 'explorer.exe', 'postgres.exe', 'postgres', 'codex.exe', 'msmpeng.exe', 'securityhealthservice.exe'}:
            return 'Operating system, database and desktop services are protected.'
        if process.username().casefold() != own.username().casefold():
            return 'Only processes owned by the current operator can be terminated.'
        return ''
    except (psutil.Error, OSError):
        return 'Process permissions or identity could not be verified.'


class Sampler:
    def __init__(self):
        self.lock = threading.Lock()
        self.cached = None
        self.sampled = 0
        self.previous = {}
        self.previous_cpu = None

    def snapshot(self):
        with self.lock:
            clock = time.monotonic()
            if self.cached and clock - self.sampled < 3:
                return self.cached
            gpu = gpu_snapshot()
            gpu_pids = {p['pid'] for p in gpu['processes']}
            processes, current = [], {}
            cores = psutil.cpu_count() or 1
            for process in psutil.process_iter(['pid', 'name', 'create_time', 'memory_info', 'cpu_times'], ad_value=None):
                info = process.info
                cpu = None
                identity = (info['pid'], info['create_time'])
                times = info['cpu_times']
                if times:
                    used = times.user + times.system
                    current[identity] = (clock, used)
                    old = self.previous.get(identity)
                    if old and clock > old[0]:
                        cpu = round(max(0, min(100, (used - old[1]) / (clock - old[0]) * 100 / cores)), 2)
                name = info['name'] or 'Access Restricted'
                category = 'Ollama' if 'ollama' in name.lower() else 'GPU' if info['pid'] in gpu_pids else 'System'
                processes.append({'pid': info['pid'], 'name': name, 'created': info['create_time'],
                                  'memory_bytes': info['memory_info'].rss if info['memory_info'] else None,
                                  'cpu_percent': cpu,
                                  'category': category, 'gpu': info['pid'] in gpu_pids})
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage(str(Path.cwd().anchor or '/'))
            cpu_times = psutil.cpu_times()
            total_cpu = sum(cpu_times)
            idle_cpu = cpu_times.idle + getattr(cpu_times, 'iowait', 0)
            cpu = None
            if self.previous_cpu:
                delta = total_cpu - self.previous_cpu[0]
                if delta > 0:
                    cpu = round(max(0, min(100, (1 - (idle_cpu - self.previous_cpu[1]) / delta) * 100)), 1)
            self.previous_cpu = (total_cpu, idle_cpu)
            self.cached = {'sampled_at': datetime.now(timezone.utc).isoformat(), 'cpu_percent': cpu,
                           'logical_cores': cores, 'memory_total': memory.total, 'memory_used': memory.total - memory.available,
                           'memory_percent': memory.percent, 'disk_free': disk.free, 'uptime_seconds': time.time() - psutil.boot_time(),
                           'gpu': gpu, 'processes': processes}
            self.previous = current
            self.sampled = clock
            return self.cached


class ProcessRequest(BaseModel):
    matter_id: str
    pid: int
    created: float


class TerminateRequest(ProcessRequest):
    token: str
    confirmation: str
    acknowledge: bool = False


class JobControl(BaseModel):
    action: Literal['pause', 'resume', 'cancel', 'retry']


def register_performance(app):
    from . import main as db
    from . import job_runtime
    from .full_corpus import control_of
    sampler, estimator, previews = Sampler(), Estimator(), {}
    preview_lock = threading.Lock()

    @app.get('/api/performance/system')
    def system():
        return sampler.snapshot()

    @app.get('/api/performance/services')
    async def services():
        result = {'application': 'Online', 'database': 'Unavailable', 'ollama': 'Unavailable', 'models': []}
        try:
            with Session(db.engine) as session:
                session.execute(text('SELECT 1'))
            result['database'] = 'Online'
        except Exception:
            pass
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.get(os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/') + '/api/ps')
                response.raise_for_status()
                result['models'] = [{key: model.get(key) for key in ('name', 'size', 'size_vram', 'context_length', 'expires_at')} for model in response.json().get('models', [])]
                result['ollama'] = 'Online'
        except (httpx.HTTPError, ValueError):
            pass
        result['indexing'] = getattr(app.state.indexer, 'states', {})
        return result

    @app.get('/api/performance/jobs')
    def jobs(matter_id: str | None = None):
        rows = []
        with Session(db.engine) as session:
            if matter_id:
                db.matter(session, matter_id)
            query = select(db.Run).order_by(db.Run.created_at.desc())
            if matter_id:
                query = query.where(db.Run.matter_id == matter_id)
            # Active work is always included, regardless of history length.
            recent = list(session.scalars(query.limit(100)))
            active = list(session.scalars(query.where(db.Run.status.in_(['running', 'paused']))))
            runs = {run.id: run for run in [*active, *recent]}
            requests = {event.details.get('run_id') for event in session.scalars(select(db.Audit).where(db.Audit.action=='job_request_saved'))}
            names = {}
            parent_ids = {run.id for run in runs.values() if run.purpose == 'full_corpus'}
            checkpoints = {}
            if parent_ids:
                extraction_query = select(db.Run).where(db.Run.purpose == 'extraction', db.Run.matter_id.in_({run.matter_id for run in runs.values()}))
                for child in session.scalars(extraction_query):
                    for item in child.manifest:
                        parent = item.get('full_run_id')
                        if parent in parent_ids and item.get('processed'):
                            checkpoints.setdefault(parent, set()).add(item.get('id'))
            for run in runs.values():
                if run.matter_id not in names:
                    workspace = session.get(db.Matter, run.matter_id)
                    names[run.matter_id] = None if not workspace or db.workspace_deleted(session, run.matter_id) else workspace.name
                if names[run.matter_id] is None:
                    continue
                meta = control_of(run)
                phase = meta.get('phase', run.purpose)
                done, total, unit = 0, 0, 'steps'
                if run.purpose in {'full_corpus', 'extraction'}:
                    sources = [dict(x) for x in run.manifest if x.get('id')]
                    if run.purpose == 'full_corpus':
                        # A historical parent's progress must not include unrelated later runs.
                        completed = checkpoints.get(run.id, set())
                        for source in sources:
                            source['processed'] = source.get('processed', False) or source.get('id') in completed
                    done, total, unit = sum(bool(x.get('processed')) for x in sources), len(sources), 'passages'
                    if phase == 'witness_drafting':
                        done = sum(x.get('kind') == 'witness' and bool(x.get('processed')) for x in run.manifest)
                        total, unit = meta.get('witness_total', 0), 'witnesses'
                progress = next((x for x in reversed(run.manifest) if x.get('kind') == 'question_progress'), None)
                benchmark=next((x for x in run.manifest if x.get('kind')=='benchmark_progress'),None)
                if benchmark:done,total,unit=benchmark.get('done',0),benchmark.get('total',0),'messages'
                if progress:
                    done, total, unit = progress.get('accepted', 0), progress.get('requested', 0), 'questions (target)'
                    phase = progress.get('phase', phase)
                if run.purpose == 'work_product':
                    done, total, unit = sum('completed_at' in x for x in run.manifest), 5, 'stages'
                current = datetime.now(timezone.utc)
                terminal = run.status not in {'running', 'paused'}
                end = run.updated_at if terminal else current
                controls = []
                runtime=job_runtime.live(run.id)
                if runtime and not runtime.get('parent_run_id'):
                    controls=['resume','cancel'] if runtime['pause_requested'] else ['pause','cancel']
                elif run.status in {'failed','partial','interrupted','cancelled'} and run.id in requests and run.purpose!='full_corpus':
                    controls=['retry']
                if run.purpose == 'full_corpus':
                    controls = ['pause', 'cancel'] if run.status == 'running' else ['resume', 'cancel'] if run.status in {'paused', 'failed'} else []
                rows.append({'id': run.id, 'matter_id': run.matter_id, 'workspace': names[run.matter_id], 'purpose': run.purpose,
                             'provider': run.provider, 'model': run.model, 'status': run.status, 'phase': phase,
                             'witness': meta.get('current_witness') or next((x.get('witness') for x in run.manifest if x.get('kind') == 'question_request'), ''),
                             'created_at': run.created_at.isoformat(), 'elapsed_seconds': max(0, (end - run.created_at).total_seconds()),
                             'checkpoint_age_seconds': max(0, (current - run.updated_at).total_seconds()),
                             'done': done, 'total': total, 'unit': unit, 'error': run.error, 'recovery_error':next((x.get('message') for x in reversed(run.manifest) if x.get('kind')=='recovery_error'),None), 'controls': controls,
                             'control_pending': bool(meta.get('pause_requested') or meta.get('cancel_requested') or (runtime and runtime['cancel_requested'])),
                             'runtime':runtime,
                             **estimator.observe(run.id, 'question_generation' if progress else phase, done, total, 'paused' if runtime and runtime['pause_requested'] else run.status)})
        return {'jobs': rows, 'history_limit': 100}

    @app.post('/api/performance/jobs/{rid}/control')
    async def job_control(rid: str, request: JobControl):
        if request.action=='retry':return await app.state.retry_job(rid)
        with Session(db.engine) as session:
            run = session.get(db.Run, rid)
            if not run:
                raise HTTPException(404, 'Job not found')
            db.matter(session, run.matter_id)
            if run.purpose != 'full_corpus':
                runtime=job_runtime.live(rid)
                if not runtime or runtime.get('parent_run_id') or not job_runtime.control(rid,request.action):raise HTTPException(409,'Job is no longer active, or its parent owns the controls.')
                db.audit(session,run.matter_id,'job_control_requested',{'run_id':rid,'action':request.action});session.commit()
                return {'run_id':rid,'message':'Control saved. It takes effect after the current model call or validated batch. In-flight provider requests may finish and incur charges.'}
            mid = run.matter_id
        return await app.state.performance_full_control(mid, request.action, rid)

    def checked(request):
        try:
            process = psutil.Process(request.pid)
            if process.create_time() != request.created:
                raise HTTPException(409, 'Process identity changed. Refresh and try again.')
            reason = protection(process)
            if reason:
                raise HTTPException(403, reason)
            return process
        except (psutil.Error, OSError):
            raise HTTPException(409, 'Process is gone or access is denied.') from None

    @app.post('/api/performance/processes/preview')
    def preview(request: ProcessRequest):
        with Session(db.engine) as session:
            db.matter(session, request.matter_id)
        process = checked(request)
        token: str = secrets.token_urlsafe(32)
        with preview_lock:
            for expired in [key for key, value in previews.items() if value['expires'] < time.monotonic()]:
                previews.pop(expired, None)
            if len(previews) >= 100:
                raise HTTPException(429, 'Too many pending confirmations')
            previews[token] = {'pid': process.pid, 'created': request.created, 'name': process.name(), 'matter_id': request.matter_id, 'expires': time.monotonic() + 60}
        return {'token': token, 'name': process.name(), 'pid': process.pid, 'confirmation': f'TERMINATE {process.pid}', 'expires_in_seconds': 60}

    @app.post('/api/performance/processes/terminate')
    def terminate(request: TerminateRequest):
        with Session(db.engine) as session:
            db.matter(session, request.matter_id)
            if not request.acknowledge or request.confirmation != f'TERMINATE {request.pid}':
                raise HTTPException(400, 'Confirm the exact PID and acknowledge possible data loss.')
            with preview_lock:
                saved = previews.pop(request.token, None)
            if not saved or saved['expires'] < time.monotonic() or any(saved[key] != getattr(request, key) for key in ('pid', 'created', 'matter_id')):
                raise HTTPException(409, 'Confirmation expired or identity changed. Preview again.')
            process = checked(request)
            if process.name() != saved['name']:
                raise HTTPException(409, 'Process identity changed.')
            # Persist the operator's request before touching the external process.
            db.audit(session, request.matter_id, 'process_termination_requested', {'pid': request.pid, 'name': saved['name'], 'created': request.created})
            session.commit()
            try:
                process.terminate()
            except (psutil.Error, OSError):
                raise HTTPException(409, 'Process could not be terminated. No elevated privileges were requested.') from None
            db.audit(session, request.matter_id, 'process_termination_signalled', {'pid': request.pid, 'name': saved['name']})
            session.commit()
        return {'message': 'Termination sent to this process only. Refresh to check whether it exited.'}
