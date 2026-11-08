"""Server-owned, resumable local index maintenance; no browser is required."""
import asyncio
import hashlib
import os
import time
from contextlib import contextmanager
from sqlalchemy import select,text
from sqlalchemy.orm import Session
from fastapi import HTTPException
from . import retrieval
from .providers import ProviderError


def indexing_mode():
    return 'manual' if os.getenv('INDEXING_MODE','automatic').lower()=='manual' else 'automatic'


class Indexer:
    def __init__(self,db):
        self.db=db;self.wake=asyncio.Event();self.lock=asyncio.Lock()
        self.states={};self.next_check={};self.task=None

    def start(self):self.task=asyncio.create_task(self.run(),name='local-corpus-indexer')

    async def close(self):
        if self.task:
            self.task.cancel()
            try:await self.task
            except asyncio.CancelledError:pass

    def changed(self):
        self.next_check.clear();self.wake.set()

    def workspace_ids(self):
        with Session(self.db.engine) as s:
            # Newest first, then one bounded batch per workspace for fairness.
            return [m.id for m in s.scalars(select(self.db.Matter).order_by(self.db.Matter.created_at.desc())) if not self.db.workspace_deleted(s,m.id)]

    def permitted(self,mid):
        with Session(self.db.engine) as s:
            m=s.get(self.db.Matter,mid)
            if not m or self.db.workspace_deleted(s,mid):return 'deleted'
            active=s.scalar(select(self.db.Run.id).where(self.db.Run.status=='running',((self.db.Run.matter_id==mid)|(self.db.Run.provider=='ollama'))).limit(1))
            return 'paused_analysis' if active else ''

    @contextmanager
    def database_lock(self,mid):
        # Session-level advisory locks also prevent duplicate indexing under multiple
        # server processes. SQL source fingerprints provide restart checkpoints.
        if self.db.engine.dialect.name!='postgresql':yield True;return
        key=int.from_bytes(hashlib.sha256(('corpus-index:'+mid).encode()).digest()[:8],'big',signed=True)
        with self.db.engine.connect() as connection:
            acquired=connection.scalar(text('SELECT pg_try_advisory_lock(:key)'),{'key':key})
            try:yield acquired
            finally:
                if acquired:connection.execute(text('SELECT pg_advisory_unlock(:key)'),{'key':key})

    def snapshot(self,mid):
        with Session(self.db.engine) as s:
            m=self.db.matter(s,mid)
            return {k:v for k,v in retrieval.index_snapshot(s,m).items() if k!='pending'}

    async def status(self,mid):
        result=await asyncio.to_thread(self.snapshot,mid)
        state=dict(self.states.get(mid,{}));phase=state.get('phase')
        if not result['remaining']:phase='complete'
        elif phase not in {'indexing','error','paused_analysis'}:phase='queued' if indexing_mode()=='automatic' else 'manual'
        if indexing_mode()=='manual' and result['remaining'] and phase not in {'indexing','error'}:phase='manual'
        return {**state,**result,'phase':phase,'mode':indexing_mode(),'worker_running':self.lock.locked(),'automatic_default':True}

    async def batch(self,mid,limit=50,automatic=False):
        if self.lock.locked():
            if not automatic:raise HTTPException(409,'Indexing is already in progress. Switch to Manual and wait for the current passage to finish before starting a manual batch.')
            return None
        async with self.lock:
            with self.database_lock(mid) as acquired:
                if not acquired:
                    self.states[mid]={'phase':'indexing','message':'Another server process is indexing this workspace.'};return None
                async def allowed():
                    if automatic and indexing_mode()!='automatic':
                        self.states[mid]={'phase':'manual','message':'Automatic indexing paused.'};return False
                    reason=await asyncio.to_thread(self.permitted,mid)
                    if reason:self.states[mid]={'phase':reason,'message':'Waiting for active analysis to finish.'};return False
                    return True
                if not await allowed():
                    if not automatic:raise HTTPException(409,'Finish active analysis before indexing this workspace.')
                    return None
                self.states[mid]={'phase':'indexing','message':'Creating masked embeddings locally.','started_at':self.db.now().isoformat()}
                def progress(count,initial):
                    self.states[mid].update(indexed_in_batch=count,current=initial['current']+count,total=initial['total'])
                try:
                    with Session(self.db.engine) as s:
                        m=self.db.matter(s,mid)
                        result=await retrieval.build_index(s,m,limit,should_continue=allowed,on_progress=progress,actor='local-indexer' if automatic else 'local-operator')
                    if not result.get('deleted'):
                        with Session(self.db.engine) as s:
                            # Lock and recheck against Trash/purge before adding audit rows.
                            m=s.scalar(select(self.db.Matter).where(self.db.Matter.id==mid).with_for_update())
                            if m and not self.db.workspace_deleted(s,mid):
                                if result['indexed']:self.db.audit(s,mid,'local_embedding_batch',{**result,'automatic':automatic})
                                s.commit()
                    if not result['stopped']:
                        self.states[mid]={'phase':'complete' if not result['remaining'] else 'queued','message':'Index is current.' if not result['remaining'] else 'Continuing with the next batch.','updated_at':self.db.now().isoformat()}
                    self.next_check[mid]=time.monotonic()+(30 if not result['remaining'] else 0)
                    return result
                except asyncio.CancelledError:raise
                except Exception as error:
                    message=str(error) if isinstance(error,ProviderError) else 'Local indexing failed. Check Ollama, the embedding model and database. Saved embeddings are retained.'
                    self.states[mid]={'phase':'error','message':message,'retry_in_seconds':60}
                    self.next_check[mid]=time.monotonic()+60
                    if not automatic:raise HTTPException(502,message) from None
                    return None

    async def cycle(self):
        for mid in await asyncio.to_thread(self.workspace_ids):
            if indexing_mode()!='automatic':break
            if time.monotonic()<self.next_check.get(mid,0):continue
            await self.batch(mid,50,automatic=True)

    async def run(self):
        while True:
            self.wake.clear()
            try:
                if indexing_mode()=='automatic':await self.cycle()
            except asyncio.CancelledError:raise
            except Exception:
                # No raw provider/database exceptions or evidence in user-visible logs.
                pass
            try:await asyncio.wait_for(self.wake.wait(),timeout=3 if any(v.get('phase')=='queued' for v in self.states.values()) else 15)
            except asyncio.TimeoutError:pass
