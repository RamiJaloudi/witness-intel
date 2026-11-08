from types import SimpleNamespace

import pytest
from scripts import launch


def test_launcher_reuses_healthy_services_without_credentials_in_commands(monkeypatch,tmp_path):
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://local_user@127.0.0.1:55439/local_db')
    pgadmin=tmp_path/'pgAdmin4.exe';pgadmin.touch()
    opened=[];components=[];urls=[]
    monkeypatch.setattr(launch,'db_ready',lambda:True)
    monkeypatch.setattr(launch,'http_ready',lambda url:True)
    monkeypatch.setattr(launch,'visible',components.append)
    monkeypatch.setattr(launch,'subprocess',SimpleNamespace(Popen=lambda argv,**kwargs:opened.append(argv)))
    monkeypatch.setattr(launch.webbrowser,'open',urls.append)
    launch.launch({'pgadmin_exe':str(pgadmin),'start_ollama':True})
    assert components==['postgres','app']
    assert opened==[[str(pgadmin)]]
    assert urls==[launch.APP_URL]


def test_launcher_refuses_remote_database(monkeypatch):
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://local_user@example.test/db')
    with pytest.raises(launch.StartupError,match='local PostgreSQL'):
        launch.db_url()


def test_launcher_never_initializes_missing_database_cluster(monkeypatch,tmp_path):
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://local_user@127.0.0.1/db')
    monkeypatch.setattr(launch,'port_open',lambda *args:False)
    with pytest.raises(launch.StartupError,match='postgres_data'):
        launch.postgres({'postgres_data':str(tmp_path/'missing')})


def test_reused_log_monitor_shows_only_new_activity(monkeypatch, tmp_path, capsys):
    log = tmp_path / 'application.log'
    log.write_text('old database timeout\n', encoding='utf-8')

    output = []

    def poll(_seconds):
        output.append(capsys.readouterr().out)
        if len(output) == 2:
            raise KeyboardInterrupt
        with log.open('a', encoding='utf-8') as stream:
            stream.write('new request succeeded\n')

    monkeypatch.setattr(launch.time, 'sleep', poll)
    with pytest.raises(KeyboardInterrupt):
        launch.monitor([log], live_only=True)
    assert 'old database timeout' not in ''.join(output)
    assert 'new request succeeded' in ''.join(output)
