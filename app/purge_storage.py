"""Constrained managed-storage staging and retryable purge cleanup.

Only UUID workspace folders under DATA_ROOT may be moved/deleted. Source input
folders are never supplied to these functions. Reparse points are refused.
"""
import json
import os
from pathlib import Path
import shutil
import stat
import uuid

class StorageSafetyError(Exception): pass

def canonical_id(value):
    try:
        result=str(uuid.UUID(str(value)))
    except (ValueError,TypeError,AttributeError): raise StorageSafetyError('Invalid workspace or operation identifier') from None
    if result!=str(value): raise StorageSafetyError('Identifier must be a canonical UUID')
    return result

def no_link(path):
    try:info=path.lstat()
    except FileNotFoundError:return
    if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',1024):
        raise StorageSafetyError('Stored folders containing links or reparse points cannot be purged automatically')

def safe_child(parent,name):
    parent=Path(parent).resolve();path=parent/name;no_link(path)
    if path.resolve().parent!=parent or path.resolve().name!=name: raise StorageSafetyError('Storage path escapes the intended folder')
    return path

def tree_stats(path):
    no_link(path)
    if not path.exists(): return {'files':0,'bytes':0}
    if not path.is_dir(): raise StorageSafetyError('Expected a managed workspace directory')
    root=path.resolve();files=0;size=0;pending=[path]
    while pending:
        current=pending.pop();no_link(current)
        for child in current.iterdir():
            no_link(child)
            if not child.resolve().is_relative_to(root): raise StorageSafetyError('Stored file escapes the workspace directory')
            if child.is_dir():pending.append(child)
            elif child.is_file():files+=1;size+=child.stat().st_size
            else:raise StorageSafetyError('Unsupported stored filesystem object')
    return {'files':files,'bytes':size}

def workspace_path(root,mid): return safe_child(Path(root).resolve(),canonical_id(mid))

def area(root,name):
    path=safe_child(Path(root).resolve(),name);path.mkdir(exist_ok=True)
    return path

def operation_path(root,opid):
    return safe_child(area(root,'.purge-pending'),canonical_id(opid))

def write_receipt(folder,receipt):
    path=safe_child(folder,'receipt.json');temporary=safe_child(folder,'receipt.tmp')
    temporary.write_text(json.dumps(receipt,indent=2),encoding='utf-8');temporary.replace(path)

def prepare(root,opid,mids,counts,created_at):
    root=Path(root).resolve()
    # Check every absolute source path and every descendant before any move.
    sources=[workspace_path(root,mid) for mid in mids]
    for source in sources:tree_stats(source)
    operation=operation_path(root,opid)
    if operation.exists():raise StorageSafetyError('Purge operation already exists')
    operation.mkdir()
    receipt={'operation_id':opid,'workspace_ids':mids,'counts':counts,'created_at':created_at,'state':'prepared'}
    write_receipt(operation,receipt);moved=[]
    try:
        for source in sources:
            destination=safe_child(operation,source.name)
            # Both absolute paths are now verified under their exact managed parents.
            if source.exists():source.rename(destination);moved.append((source,destination))
    except Exception:
        rollback(root,opid,mids)
        raise
    return receipt

def rollback(root,opid,mids):
    operation=operation_path(root,opid)
    if not operation.exists():return
    for mid in reversed(mids):
        staged=safe_child(operation,canonical_id(mid));source=workspace_path(root,mid)
        if staged.exists():
            tree_stats(staged)
            if source.exists():raise StorageSafetyError('Cannot restore staged storage over an existing directory')
            staged.rename(source)
    for name in ('receipt.json','receipt.tmp'):
        receipt=safe_child(operation,name)
        if receipt.exists():receipt.unlink()
    operation.rmdir()

def load_receipt(root,opid):
    operation=operation_path(root,opid)
    path=safe_child(operation,'receipt.json')
    if not path.exists():raise StorageSafetyError('Pending purge operation not found')
    receipt=json.loads(path.read_text(encoding='utf-8'))
    if receipt.get('operation_id')!=canonical_id(opid):raise StorageSafetyError('Invalid purge receipt')
    for mid in receipt.get('workspace_ids',[]):canonical_id(mid)
    return operation,receipt

def mark_committed(root,opid):
    operation,receipt=load_receipt(root,opid);receipt['state']='database_purged';write_receipt(operation,receipt)

def cleanup(root,opid,completed_at):
    operation,receipt=load_receipt(root,opid)
    if receipt['state'] not in {'prepared','database_purged'}:raise StorageSafetyError('Invalid purge state')
    # Caller must verify that every workspace ID is absent from the database.
    remaining=[]
    for mid in receipt['workspace_ids']:
        target=safe_child(operation,mid)
        if target.exists():
            try:
                tree_stats(target)  # Recheck resolved absolute target/tree immediately before deletion.
                shutil.rmtree(target)
            except (OSError,StorageSafetyError):remaining.append(mid)
    if remaining:
        receipt.update(state='database_purged',pending_workspace_ids=remaining);write_receipt(operation,receipt)
        return {'completed':False,'operation_id':opid,'pending_file_workspaces':len(remaining)}
    receipt.update(state='complete',completed_at=completed_at,pending_workspace_ids=[])
    receipts=area(root,'.purge-receipts');destination=safe_child(receipts,opid+'.json')
    # Minimal ledger: UUIDs/counts/timestamps only, no source text, names or credentials.
    destination.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    for name in ('receipt.json','receipt.tmp'):
        path=safe_child(operation,name)
        if path.exists():path.unlink()
    operation.rmdir()
    return {'completed':True,'operation_id':opid,'pending_file_workspaces':0}

def pending_operations(root):
    parent=area(root,'.purge-pending');result=[]
    for child in parent.iterdir():
        if not child.is_dir():continue
        try:
            _,receipt=load_receipt(root,child.name)
            result.append({'operation_id':receipt['operation_id'],'workspace_ids':receipt['workspace_ids'],'state':receipt['state']})
        except (OSError,ValueError,StorageSafetyError):continue
    return result
