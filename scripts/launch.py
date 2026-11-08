"""Windows desktop launcher. Local configuration and credentials never go in argv."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'data' / 'launcher.json'
APP_URL = 'http://127.0.0.1:8765'


class StartupError(Exception):
    pass


def configuration():
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    config = json.loads(CONFIG.read_text(encoding='utf-8')) if CONFIG.exists() else {}
    if config.get('database_url'):
        os.environ['DATABASE_URL'] = config['database_url']
    return config


def db_url():
    from sqlalchemy.engine import make_url
    url = make_url(os.environ.get('DATABASE_URL', 'postgresql+psycopg://localhost/witness_ai'))
    if url.host not in {'localhost', '127.0.0.1', '::1'}:
        raise StartupError('This desktop launcher requires a local PostgreSQL host.')
    return url


def port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def db_ready():
    from sqlalchemy import create_engine, text
    engine = create_engine(db_url(), connect_args={'connect_timeout': 2})
    try:
        with engine.connect() as connection:
            connection.execute(text('SELECT 1'))
        return True
    except Exception:
        return False  # Never print a URL, password, or driver exception.
    finally:
        engine.dispose()


def http_ready(url):
    import httpx
    try:
        response = httpx.get(url, timeout=3, trust_env=False)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def wait_for(check, label, seconds=90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            print(label + ' ready.', flush=True)
            return
        time.sleep(2)
    raise StartupError(label + ' did not become ready. Inspect its visible console and local configuration.')


def visible(component):
    if os.name != 'nt':
        raise StartupError('Visible console launching is supported on Windows only.')
    return subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()), '--component', component],
                            cwd=ROOT, creationflags=subprocess.CREATE_NEW_CONSOLE)


def monitor(paths, *, live_only=False):
    # Reused services can have old failures in their logs. Show only new activity.
    positions = {path: path.stat().st_size for path in paths if path.exists()} if live_only else {}
    print('Live log monitor. Closing this monitor does not stop an existing service.', flush=True)
    while True:
        for path in paths:
            if not path.exists():
                continue
            size = path.stat().st_size
            offset = positions.get(path, max(0, size - 12000))
            if size < offset:
                offset = 0
            with path.open('rb') as stream:
                stream.seek(offset)
                data = stream.read()
                positions[path] = stream.tell()
            if data:
                print('[' + path.name + ']\n' + data.decode('utf-8', errors='replace'), end='', flush=True)
        time.sleep(1)


def postgres(config):
    url = db_url()
    host, port = url.host, url.port or 5432
    already_running = port_open(host, port)
    if not already_running:
        data = Path(config.get('postgres_data') or '')
        binary = Path(config.get('postgres_bin', '')) / 'pg_ctl.exe'
        if not config.get('postgres_data') or not (data / 'PG_VERSION').is_file() or not binary.is_file():
            raise StartupError('Set postgres_bin and postgres_data in data/launcher.json, or start your existing PostgreSQL service.')
        log = ROOT / 'data' / 'postgres-launcher.log'
        print('Starting the existing PostgreSQL cluster; no database will be created or reset.', flush=True)
        result = subprocess.run([str(binary), '-D', str(data), '-l', str(log),
                                 '-o', f'-h 127.0.0.1 -p {port}', '-w', '-t', '60', 'start'], cwd=ROOT)
        if result.returncode:
            raise StartupError('PostgreSQL startup failed. Inspect data/postgres-launcher.log.')
    wait_for(db_ready, 'PostgreSQL')
    print(f'Connected to local database {url.database} on port {port}.', flush=True)
    logs = [ROOT / 'data' / 'postgres-launcher.log']
    data = Path(config.get('postgres_data') or ROOT / 'data')
    current = data / 'current_logfiles'
    if current.exists():
        for line in current.read_text(encoding='utf-8').splitlines():
            if ' ' in line:
                logs.append(data / line.split(' ', 1)[1])
    monitor(logs, live_only=already_running)


def application():
    if http_ready(APP_URL + '/api/health'):
        print('Application already running. Reusing it without interrupting active requests.', flush=True)
        logs=[ROOT / 'data' / 'application-launcher.log']
        monitor(
            logs if logs[0].exists() else [ROOT / 'data' / 'preview-out.log', ROOT / 'data' / 'preview-error.log'],
            live_only=True,
        )
    elif port_open('127.0.0.1', 8765):
        raise StartupError('Port 8765 is occupied but application health failed. Inspect the existing server before restarting.')
    else:
        print('Starting Witness Intel. This window shows live Python server output.', flush=True)
        print('Keep this window open while using the application.', flush=True)
        process = subprocess.Popen([sys.executable, '-u', '-m', 'uvicorn', 'app.main:app',
                                    '--host', '127.0.0.1', '--port', '8765'], cwd=ROOT,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding='utf-8', errors='replace', bufsize=1)
        (ROOT / 'data' / 'launcher-app.pid').write_text(str(process.pid), encoding='utf-8')
        with (ROOT / 'data' / 'application-launcher.log').open('a', encoding='utf-8') as log:
            for line in process.stdout:
                print(line, end='', flush=True)
                log.write(line);log.flush()
        if process.wait():
            raise StartupError('Application server exited with an error; inspect the output above.')


def ollama():
    executable = shutil.which('ollama')
    if not executable:
        raise StartupError('Ollama was not found on PATH. Install it or set start_ollama=false locally.')
    result = subprocess.run([executable, 'serve'], cwd=ROOT)
    if result.returncode:
        raise StartupError('Ollama exited; inspect its output above.')


def launch(config):
    print('Witness Intel - desktop startup', flush=True)
    db_url()  # Validate before launching any components.
    visible('postgres')
    wait_for(db_ready, 'PostgreSQL')
    if config.get('start_ollama', True):
        endpoint = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')
        if endpoint not in {'http://127.0.0.1:11434', 'http://localhost:11434'}:
            raise StartupError('Automatic Ollama startup requires its standard local endpoint.')
        if not http_ready(endpoint + '/api/tags'):
            visible('ollama')
            wait_for(lambda: http_ready(endpoint + '/api/tags'), 'Ollama')
        else:
            print('Ollama already running; reusing it.', flush=True)
    visible('app')
    wait_for(lambda: http_ready(APP_URL + '/api/health'), 'Application', seconds=120)
    pgadmin = Path(config.get('pgadmin_exe') or 'C:/Program Files/PostgreSQL/18/pgAdmin 4/runtime/pgAdmin4.exe')
    if pgadmin.is_file():
        try:
            subprocess.Popen([str(pgadmin)], cwd=pgadmin.parent)
        except OSError:
            print('Could not open pgAdmin. You can open it separately.', flush=True)
        else:
            print('Opened pgAdmin using its existing profile and saved credentials.', flush=True)
            print('If pgAdmin requests a master password or sign-in, unlock it in pgAdmin.', flush=True)
    else:
        print('pgAdmin was not found. Set pgadmin_exe in data/launcher.json.', flush=True)
    if config.get('open_browser', True):
        try:
            opened = webbrowser.open(APP_URL)
        except (OSError, webbrowser.Error):
            opened = False
        if opened is False:
            print('Could not open the default browser. Open the Ready address below.', flush=True)
    print('Ready: ' + APP_URL, flush=True)
    print('Application/PostgreSQL consoles remain visible. No credentials were printed.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--component', choices=['postgres', 'app', 'ollama'])
    parser.add_argument('--check', action='store_true', help='Check local prerequisites without starting programs')
    args = parser.parse_args()
    os.chdir(ROOT)
    try:
        config = configuration()
        if os.name == 'nt':
            ctypes.windll.kernel32.SetConsoleTitleW('Witness Intel - ' + (args.component or 'Launcher'))
        if args.check:
            url = db_url()
            ollama_url = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')
            local_ollama = ollama_url in {'http://127.0.0.1:11434', 'http://localhost:11434'}
            print(json.dumps({'database_ready': db_ready(), 'database_port': url.port or 5432,
                              'application_ready': http_ready(APP_URL + '/api/health'),
                              'pgadmin_found': Path(config.get('pgadmin_exe', '')).is_file(),
                              'ollama_found': bool(shutil.which('ollama')),
                              'ollama_ready': local_ollama and http_ready(ollama_url + '/api/tags')}))
        elif args.component:
            {'postgres': lambda: postgres(config), 'app': application, 'ollama': ollama}[args.component]()
        else:
            launch(config)
    except KeyboardInterrupt:
        print('\nConsole stopped. PostgreSQL and previously running services are not shut down automatically.')
    except Exception as error:
        if isinstance(error, StartupError):
            print(str(error), flush=True)
        # Do not print raw exceptions: database URLs/credentials may be embedded.
        print('Startup failed. Check paths in data/launcher.json, database authentication, and the visible component output.', flush=True)
        if args.component:
            input('Press Enter to close this console...')
        sys.exit(1)
