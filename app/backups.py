"""Portable, checksum-verified backups restored only into new workspace IDs."""
import hashlib
import json
import os
import secrets
import shutil
import threading
import tempfile
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from fastapi import HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

SETTINGS = ['UI_PALETTE', 'ANALYSIS_MODE', 'INDEXING_MODE', 'PRIVACY_REVIEW_REQUIRED', 'OLLAMA_MODEL', 'GEMINI_MODEL', 'OPENAI_MODEL', 'ANTHROPIC_MODEL']
MAX_UPLOAD = 512 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024

def file_hash(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source,'sha256').hexdigest()


class BackupRequest(BaseModel):
    matter_id: str | None = None
    all_workspaces: bool = False
    operation_id: str | None = None


class RestoreRequest(BaseModel):
    token: str
    confirmation: str
    apply_settings: bool = False


def validated_settings(values):
    from .palettes import palettes
    allowed = {'UI_PALETTE': {item['id'] for item in palettes()}, 'ANALYSIS_MODE': {'full', 'manual'},
               'INDEXING_MODE': {'automatic', 'manual'}, 'PRIVACY_REVIEW_REQUIRED': {'true', 'false', '1', '0', 'yes', 'no'}}
    result = {}
    if not isinstance(values, dict) or set(values) - set(SETTINGS):
        raise HTTPException(400, 'Backup contains unsupported settings.')
    for key, value in values.items():
        if value is None:
            continue
        if not isinstance(value, str) or not 1 <= len(value) <= 200 or any(ord(char) < 32 for char in value):
            raise HTTPException(400, 'Backup setting is invalid.')
        if key in allowed and value not in allowed[key]:
            raise HTTPException(400, 'Backup setting is not supported by this version.')
        result[key] = value
    return result


def apply_settings(db, values):
    """Prepare a complete config copy, preserving secrets, then replace it atomically."""
    from dotenv import set_key
    directory = db.ROOT / 'restore_uploads'; directory.mkdir(parents=True, exist_ok=True)
    handle, filename = tempfile.mkstemp(prefix='settings-', suffix='.env', dir=directory)
    os.close(handle); temporary = Path(filename)
    try:
        temporary.write_bytes(db.ENV_PATH.read_bytes() if db.ENV_PATH.exists() else b'')
        for key, value in values.items():
            set_key(str(temporary), key, value)
        os.replace(temporary, db.ENV_PATH)
        for key, value in values.items():
            os.environ[key] = value
    finally:
        temporary.unlink(missing_ok=True)


def remap(value, identifiers):
    if isinstance(value, str):
        return identifiers.get(value, value)
    if isinstance(value, list):
        return [remap(item, identifiers) for item in value]
    if isinstance(value, dict):
        return {identifiers.get(key, key): remap(item, identifiers) for key, item in value.items()}
    return value


def validate_archive(path):
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            if len(members) > 30000 or len(set(names)) != len(names) or sum(member.file_size for member in members) > MAX_EXPANDED:
                raise ValueError('Archive exceeds size limits or has duplicate entries')
            for member in members:
                relative = PurePosixPath(member.filename)
                if relative.is_absolute() or '..' in relative.parts or '\\' in member.filename or ':' in member.filename or member.file_size > MAX_UPLOAD:
                    raise ValueError('Unsafe archive path or oversized file')
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('Symbolic links are not supported')
            if archive.getinfo('manifest.json').file_size > 50 * 1024 * 1024:
                raise ValueError('Manifest is too large')
            manifest = json.loads(archive.read('manifest.json'))
            if not isinstance(manifest,dict) or manifest.get('format') != 'witness-intel-backup-v1':
                raise ValueError('Unsupported backup format')
            if not isinstance(manifest,dict) or not isinstance(manifest.get('tables'), dict) or not manifest['tables'].get('matters'):
                raise ValueError('No workspace records')
            if any(not isinstance(rows,list) or any(not isinstance(row,dict) or not isinstance(row.get('id'),str) for row in rows) for rows in manifest['tables'].values()):
                raise ValueError('Invalid table records')
            if not isinstance(manifest.get('files'),dict):raise ValueError('Invalid file manifest')
            expected_names = {'manifest.json', *manifest.get('files', {})}
            if set(names) != expected_names:
                raise ValueError('Unexpected or missing archive entries')
            for name, expected in manifest['files'].items():
                digest = hashlib.sha256()
                with archive.open(name) as source:
                    while data := source.read(1024 * 1024):
                        digest.update(data)
                if digest.hexdigest() != expected['sha256']:
                    raise ValueError('Original checksum mismatch')
            return manifest
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError):
        raise HTTPException(400, 'Invalid, incomplete or unsafe backup. Nothing was restored.') from None


def register_backups(app):
    from . import main as db
    pending, lock = {}, threading.Lock()
    progress, progress_lock = {}, threading.Lock()
    classes = [db.Matter, db.Document, db.Chunk, db.Run, db.Output, db.Audit, db.Embedding]

    def update_progress(operation_id, phase, done=0, total=0):
        with progress_lock:
            if len(progress) >= 100 and operation_id not in progress:
                progress.pop(next(iter(progress)))
            progress[operation_id] = {'phase': phase, 'done': done, 'total': total}

    @app.get('/api/backups/progress/{operation_id}')
    def backup_progress(operation_id: str):
        with progress_lock:
            return dict(progress.get(operation_id, {'phase': 'Preparing', 'done': 0, 'total': 0}))

    @app.get('/api/backups')
    def list_backups():
        directory = db.ROOT / 'backups'
        result = []
        for path in sorted(directory.glob('*.zip'), key=lambda item: item.stat().st_mtime, reverse=True)[:20]:
            try:
                bid = str(uuid.UUID(path.stem))
            except ValueError:
                continue
            result.append({'backup_id': bid, 'bytes': path.stat().st_size, 'created_at': datetime.fromtimestamp(path.stat().st_mtime).isoformat(), 'download_url': f'/api/backups/{bid}/download'})
        return {'backups': result}

    @app.post('/api/backups')
    def create_backup(request: BackupRequest):
        directory = db.ROOT / 'backups'; directory.mkdir(parents=True, exist_ok=True)
        bid = str(uuid.uuid4()); path = directory / (bid + '.zip')
        try:
            operation_id = str(uuid.UUID(request.operation_id)) if request.operation_id else bid
        except ValueError:
            raise HTTPException(400, 'Invalid backup operation ID') from None
        update_progress(operation_id, 'Gathering Consistent Database Records')
        try:
            with db.engine.connect() as connection:
                if db.engine.dialect.name == 'postgresql':
                    connection = connection.execution_options(isolation_level='REPEATABLE READ')
                with Session(connection) as session:
                    if request.all_workspaces:
                        workspaces = list(session.scalars(select(db.Matter)))
                    elif request.matter_id:
                        workspaces = [db.matter(session, request.matter_id)]
                    else:
                        raise HTTPException(400, 'Select a workspace or choose all workspaces.')
                    ids = {workspace.id for workspace in workspaces}
                    if not ids:
                        raise HTTPException(400, 'No workspaces to back up.')
                    if session.scalar(select(db.Run.id).where(db.Run.matter_id.in_(ids), db.Run.status.in_(['running', 'paused'])).limit(1)):
                        raise HTTPException(409, 'Finish or cancel active jobs before taking a consistent backup.')
                    tables = {'matters': [db.pack(workspace) for workspace in workspaces]}
                    documents = list(session.scalars(select(db.Document).where(db.Document.matter_id.in_(ids))))
                    doc_ids = {document.id for document in documents}
                    for model in classes[1:]:
                        query = select(model).where(model.document_id.in_(doc_ids)) if model is db.Chunk else select(model).where(model.matter_id.in_(ids))
                        records = []
                        for record in session.scalars(query):
                            packed = db.pack(record)
                            if model is db.Embedding:
                                packed['vector'] = [float(value) for value in record.vector]
                            records.append(packed)
                        tables[model.__tablename__] = records
                    from .palettes import selected_palette
                    from .full_corpus import analysis_mode
                    from .indexer import indexing_mode
                    from .providers import PROVIDERS
                    settings_snapshot={'UI_PALETTE':selected_palette(),'ANALYSIS_MODE':analysis_mode(),'INDEXING_MODE':indexing_mode(),'PRIVACY_REVIEW_REQUIRED':'true' if db.privacy_review_required() else 'false'}
                    settings_snapshot.update({config['model_env']:os.getenv(config['model_env'],config['default_model']) for config in PROVIDERS.values()})
                    manifest = {'format': 'witness-intel-backup-v1', 'created_at': db.now().isoformat(), 'tables': tables,
                                'settings': settings_snapshot, 'files': {}, 'credentials_included': False}
                    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                        for index, document in enumerate(documents):
                            update_progress(operation_id, 'Copying And Hashing Originals', index, len(documents))
                            folder = (db.ROOT / document.matter_id / document.id).resolve()
                            if not folder.is_relative_to(db.ROOT.resolve()):
                                raise HTTPException(409, 'Evidence path is outside local storage.')
                            candidates = list(folder.glob('original*')) if folder.is_dir() else []
                            original = next((item for item in candidates if item.is_file() and item.resolve().is_relative_to(folder) and (item.name == 'original' or item.name.startswith('original.'))), None)
                            if original is None:
                                raise HTTPException(409, 'A preserved original is missing. Resolve the missing evidence before backing up.')
                            digest = file_hash(original)
                            if digest != document.sha256:
                                raise HTTPException(409, 'An original file differs from its recorded hash. No backup was published.')
                            name = f'originals/{document.matter_id}/{document.id}/{original.name}'
                            archive.write(original, name)
                            manifest['files'][name] = {'sha256': digest, 'document_id': document.id, 'matter_id': document.matter_id}
                        archive.writestr('manifest.json', json.dumps(manifest, default=str, ensure_ascii=False))
            update_progress(operation_id, 'Verifying Backup Checksums', len(documents), len(documents))
            validate_archive(path)
            update_progress(operation_id, 'Completed', len(documents), len(documents))
            return {'backup_id': bid, 'workspaces': len(workspaces), 'documents': len(documents), 'bytes': path.stat().st_size,
                    'download_url': f'/api/backups/{bid}/download', 'message': 'Checksum-verified backup created. API keys, database passwords and privacy secrets are excluded.'}
        except Exception:
            update_progress(operation_id, 'Failed · No Backup Published')
            path.unlink(missing_ok=True)
            raise

    @app.get('/api/backups/{bid}/download')
    def download(bid: str):
        try:
            bid = str(uuid.UUID(bid))
        except ValueError:
            raise HTTPException(404, 'Backup not found') from None
        path = db.ROOT / 'backups' / (bid + '.zip')
        if not path.is_file():
            raise HTTPException(404, 'Backup not found')
        return FileResponse(path, filename=f'Witness-Intel-Backup-{bid}.zip', media_type='application/zip')

    @app.post('/api/backups/preview')
    async def preview(file: UploadFile = File(...)):
        directory = db.ROOT / 'restore_uploads'; directory.mkdir(parents=True, exist_ok=True)
        token: str = secrets.token_urlsafe(32); path = directory / (uuid.uuid4().hex + '.zip')
        size = 0
        try:
            with path.open('wb') as destination:
                while data := await file.read(1024 * 1024):
                    size += len(data)
                    if size > MAX_UPLOAD:
                        raise HTTPException(413, 'Restore upload exceeds 512 MB.')
                    destination.write(data)
            manifest = await run_in_threadpool(validate_archive, path)
            with lock:
                for old in [key for key, entry in pending.items() if entry['expires'] < time.monotonic()]:
                    pending.pop(old)['path'].unlink(missing_ok=True)
                if len(pending) >= 10:
                    raise HTTPException(429, 'Too many pending restore previews.')
                pending[token] = {'path': path, 'expires': time.monotonic() + 3600}
            return {'token': token, 'workspaces': [workspace['name'] for workspace in manifest['tables']['matters']],
                    'documents': len(manifest['tables'].get('documents', [])), 'settings': manifest.get('settings', {}),
                    'message': 'All checksums passed. Restore creates new workspace copies; existing evidence and settings are not overwritten. Credentials must be configured separately.'}
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.post('/api/backups/restore')
    def restore(request: RestoreRequest):
        if request.confirmation != 'RESTORE AS NEW WORKSPACES':
            raise HTTPException(400, 'Type RESTORE AS NEW WORKSPACES to confirm.')
        with lock:
            preview = pending.pop(request.token, None)
        if not preview or preview['expires'] < time.monotonic():
            raise HTTPException(409, 'Restore preview expired. Upload the backup again.')
        path, created, committed = preview['path'], [], False
        try:
            manifest = validate_archive(path)
            settings_to_apply = validated_settings(manifest.get('settings', {})) if request.apply_settings else {}
            tables = manifest['tables']
            all_records = [record for records in tables.values() for record in records]
            identifiers = {record['id']: str(uuid.uuid4()) for record in all_records}
            if len(identifiers) != len(all_records):
                raise HTTPException(400, 'Duplicate record IDs in backup.')
            known_tables = {model.__tablename__ for model in classes}
            if set(tables) != known_tables:
                raise HTTPException(400, 'Unexpected backup tables.')
            workspace_ids = {row['id'] for row in tables['matters']}
            documents = {row['id']: row for row in tables['documents']}
            run_ids = {row['id'] for row in tables['runs']}
            chunk_ids = {row['id'] for row in tables['chunks']}
            for model in classes:
                for record in tables[model.__tablename__]:
                    if model is not db.Matter and model is not db.Chunk and record.get('matter_id') not in workspace_ids:
                        raise HTTPException(400, 'Invalid workspace reference.')
                    if model is db.Chunk and record.get('document_id') not in documents:
                        raise HTTPException(400, 'Invalid document reference.')
                    if model is db.Output and record.get('run_id') not in run_ids:
                        raise HTTPException(400, 'Invalid job reference.')
                    if model is db.Embedding and record.get('chunk_id') not in chunk_ids:
                        raise HTTPException(400, 'Invalid embedding reference.')
            if len(manifest['files'])!=len(documents) or {meta['document_id'] for meta in manifest['files'].values()} != set(documents):
                raise HTTPException(400, 'Original files do not cover every document.')
            with Session(db.engine) as session, zipfile.ZipFile(path) as archive:
                for row in tables['matters']:
                    folder = (db.ROOT / identifiers[row['id']]).resolve()
                    if not folder.is_relative_to(db.ROOT.resolve()) or folder.exists():
                        raise HTTPException(409, 'Restore destination is unavailable.')
                    folder.mkdir(parents=True); created.append(folder)
                for name, metadata in manifest['files'].items():
                    document = documents[metadata['document_id']]
                    if metadata['matter_id'] != document['matter_id'] or metadata['sha256'] != document['sha256']:
                        raise HTTPException(400, 'Original does not match its document record.')
                    basename = PurePosixPath(name).name
                    if not (basename == 'original' or basename.startswith('original.')):
                        raise HTTPException(400, 'Invalid original filename.')
                    target = db.ROOT / identifiers[document['matter_id']] / identifiers[document['id']] / basename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(name) as source, target.open('xb') as destination:
                        shutil.copyfileobj(source, destination)
                for model in classes:
                    columns = {column.name for column in model.__table__.columns}
                    for record in tables[model.__tablename__]:
                        packed = remap(record, identifiers)
                        if set(packed) != columns:
                            raise HTTPException(400, 'Backup schema differs from this application version.')
                        for key in ('created_at', 'updated_at'):
                            packed[key] = datetime.fromisoformat(packed[key])
                        if model is db.Matter:
                            packed['name'] += ' (Restored)'
                        if model is db.Run and packed['status'] in {'running', 'paused'}:
                            packed['status'] = 'interrupted'; packed['error'] = 'Restored checkpoint. Retry explicitly; jobs never start automatically during restore.'
                        session.add(model(**packed))
                    session.flush()
                for row in tables['matters']:
                    db.audit(session, identifiers[row['id']], 'backup_restored', {'source_workspace_id': row['id'], 'settings_snapshot': manifest.get('settings', {}), 'credentials_restored': False})
                session.commit()
                committed = True
            settings_applied = False
            settings_notice = 'Settings snapshot retained; current server settings remain in effect.'
            if request.apply_settings:
                try:
                    apply_settings(db, settings_to_apply)
                    settings_applied = True
                    settings_notice = 'Non-secret global settings restored. Existing API keys and server secrets were preserved.'
                except OSError:
                    settings_notice = 'Workspace restore succeeded, but settings could not be written. Current server settings remain in effect; apply the saved snapshot manually.'
            return {'workspace_ids': [identifiers[row['id']] for row in tables['matters']], 'settings_applied': settings_applied,
                    'message': 'Restored into new workspace copies. Original files, review history and saved output references were verified. ' + settings_notice}
        except Exception:
            # Delete only folders created by this restore, with checked absolute destinations.
            for folder in created:
                if not committed and folder.is_relative_to(db.ROOT.resolve()) and folder.name in identifiers.values():
                    shutil.rmtree(folder)
            raise
        finally:
            path.unlink(missing_ok=True)
