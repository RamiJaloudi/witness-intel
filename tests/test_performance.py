import os
from types import SimpleNamespace

import psutil
from sqlalchemy.orm import Session

from app import main, performance
from tests.test_workflow import client, create


def test_estimates_require_progress_and_reset():
    estimate = performance.Estimator()
    assert estimate.observe('a', 'extract', 1, 100, 'running', 0)['eta_seconds'] is None
    estimate.observe('a', 'extract', 2, 100, 'running', 5)
    result = estimate.observe('a', 'extract', 3, 100, 'running', 10)
    assert result['rate_per_minute'] == 12
    assert result['eta_range_seconds'] == [242.5, 970]
    assert estimate.observe('a', 'extract', 3, 100, 'running', 110)['eta_seconds'] is None
    assert estimate.observe('a', 'draft', 3, 100, 'running', 115)['eta_seconds'] is None
    assert estimate.observe('a', 'draft', 3, 100, 'paused', 120)['eta_seconds'] is None


def test_nvidia_na_and_compute_failure(monkeypatch):
    monkeypatch.setattr(performance.shutil, 'which', lambda _: 'nvidia-smi')
    def fake(args, **kwargs):
        assert kwargs['timeout'] == 3
        if 'compute-apps' in args[1]:
            return SimpleNamespace(stdout='GPU-1, 123, [N/A]\n')
        return SimpleNamespace(stdout='0, GPU-1, Test GPU, 999, 75, 200, 1000, N/A, 50, 100\n')
    monkeypatch.setattr(performance.subprocess, 'run', fake)
    result = performance.gpu_snapshot()
    assert result['devices'][0]['temperature.gpu'] is None
    assert result['devices'][0]['utilization.gpu'] == 75
    assert result['processes'][0]['memory_mb'] is None


def test_protected_processes():
    assert performance.protection(psutil.Process(os.getpid()))
    process = SimpleNamespace(pid=999999, name=lambda: 'postgres.exe')
    assert performance.protection(process)


def test_process_confirmation_identity_and_single_use(client, monkeypatch):
    mid = create(client)
    calls = []
    process = SimpleNamespace(pid=999999, create_time=lambda: 123.5, name=lambda: 'synthetic-worker.exe', terminate=lambda: calls.append(True))
    monkeypatch.setattr(performance.psutil, 'Process', lambda pid: process)
    monkeypatch.setattr(performance, 'protection', lambda _: '')
    request = {'matter_id': mid, 'pid': process.pid, 'created': 123.5}
    assert client.post('/api/performance/processes/preview', json={**request, 'created': 123}).status_code == 409
    preview = client.post('/api/performance/processes/preview', json=request)
    assert preview.status_code == 200
    body = {**request, 'token': preview.json()['token'], 'confirmation': 'wrong', 'acknowledge': True}
    assert client.post('/api/performance/processes/terminate', json=body).status_code == 400
    assert not calls
    body['confirmation'] = f'TERMINATE {process.pid}'
    assert client.post('/api/performance/processes/terminate', json=body).status_code == 200
    assert client.post('/api/performance/processes/terminate', json=body).status_code == 409
    assert calls == [True]


def test_jobs_scope_and_targeted_control(client):
    mid, other = create(client), create(client)
    with Session(main.engine) as session:
        old = main.Run(matter_id=mid, provider='ollama', model='test', purpose='full_corpus', status='paused', manifest=[{'kind':'control','phase':'extraction'}])
        newer = main.Run(matter_id=mid, provider='ollama', model='test', purpose='full_corpus', status='failed', manifest=[])
        foreign = main.Run(matter_id=other, provider='ollama', model='test', purpose='questions', status='completed', manifest=[])
        session.add_all([old, newer, foreign]); session.commit()
        old_id, new_id, foreign_id = old.id, newer.id, foreign.id
    jobs = client.get('/api/performance/jobs', params={'matter_id': mid}).json()['jobs']
    assert {job['id'] for job in jobs} == {old_id, new_id}
    assert client.post(f'/api/performance/jobs/{foreign_id}/control', json={'action':'cancel'}).status_code == 409
    assert client.post(f'/api/performance/jobs/{old_id}/control', json={'action':'cancel'}).status_code == 200
    with Session(main.engine) as session:
        assert session.get(main.Run, old_id).status == 'cancelled'
        assert session.get(main.Run, new_id).status == 'failed'


def test_sampler_does_not_expose_command_lines(monkeypatch):
    monkeypatch.setattr(performance, 'gpu_snapshot', lambda: {'devices':[], 'processes':[], 'message':'test'})
    snapshot = performance.Sampler().snapshot()
    assert snapshot['processes']
    assert snapshot['cpu_percent'] is None
    for process in snapshot['processes']:
        assert not {'cmdline', 'environ', 'username', 'exe'} & process.keys()


def test_termination_rechecks_protection_and_expiry(client, monkeypatch):
    mid = create(client)
    calls = []
    process = SimpleNamespace(pid=999998, create_time=lambda: 456.0, name=lambda: 'synthetic-worker.exe', terminate=lambda: calls.append(True))
    monkeypatch.setattr(performance.psutil, 'Process', lambda pid: process)
    monkeypatch.setattr(performance, 'protection', lambda _: '')
    body = {'matter_id': mid, 'pid': process.pid, 'created': 456.0}
    token = client.post('/api/performance/processes/preview', json=body).json()['token']
    monkeypatch.setattr(performance, 'protection', lambda _: 'Protected now')
    request = {**body, 'token': token, 'confirmation': f'TERMINATE {process.pid}', 'acknowledge': True}
    assert client.post('/api/performance/processes/terminate', json=request).status_code == 403
    monkeypatch.setattr(performance, 'protection', lambda _: '')
    token = client.post('/api/performance/processes/preview', json=body).json()['token']
    clock = performance.time.monotonic()
    monkeypatch.setattr(performance.time, 'monotonic', lambda: clock + 61)
    assert client.post('/api/performance/processes/terminate', json={**request, 'token': token}).status_code == 409
    assert not calls
