"""Persistent full-corpus extraction followed by per-witness synthesis."""
from .providers import Provider
import asyncio
import hashlib
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime,timezone,timedelta
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel,Field
from sqlalchemy import select,text
from sqlalchemy.orm import Session
from .providers import active_model,configured_model

full_parent=ContextVar('full_corpus_parent',default=None)
ACTIVE_STATUSES=('running','paused')
def analysis_mode():return 'manual' if os.getenv('ANALYSIS_MODE','full')=='manual' else 'full'

class FullAnalysisInput(BaseModel):
    provider:Provider='ollama'
    batch_size:int=Field(default=25,ge=1,le=500)
    draft_work_product:bool=True
    question_settings:dict=Field(default_factory=dict)


def control_of(run):return next((dict(item) for item in run.manifest if item.get('kind')=='control'),{})
def update_control(run,**updates):run.manifest=[{**control_of(run),**updates,'kind':'control'},*[item for item in run.manifest if item.get('kind')!='control']]
def control_requested(engine):
    from . import main as db
    rid=full_parent.get()
    if not rid:return ''
    with Session(engine) as s:
        run=s.get(db.Run,rid)
        if not run:return 'cancel'
        meta=control_of(run)
        return 'cancel' if meta.get('cancel_requested') else 'pause' if meta.get('pause_requested') or run.status=='paused' else ''

def ensure_no_full_job(s,mid):
    from . import main as db
    query=select(db.Run.id).where(db.Run.matter_id==mid,db.Run.purpose=='full_corpus',db.Run.status.in_(ACTIVE_STATUSES),db.Run.id!=(full_parent.get() or ''))
    if s.scalar(query.limit(1)):raise HTTPException(409,'A full-corpus analysis owns this workspace. Pause/cancel it before starting another workflow.')


def source_scope(s,mid,provider,model,validate=True):
    from . import main as db
    m=db.matter(s,mid)
    docs=list(s.scalars(select(db.Document).where(db.Document.matter_id==mid,db.Document.status!='excluded')))
    if validate and not docs:raise HTTPException(400,'Import documents before full-corpus analysis.')
    if validate and any(d.status=='failed' for d in docs):raise HTTPException(400,'Resolve or explicitly exclude failed documents before full-corpus analysis.')
    chunks=list(s.scalars(select(db.Chunk).join(db.Document).where(db.Document.matter_id==mid,db.Document.status!='excluded').order_by(db.Document.created_at,db.Chunk.created_at,db.Chunk.id)))
    if validate and not chunks:raise HTTPException(400,'No readable source passages are available.')
    if validate and db.privacy_review_required() and any(not c.approved for c in chunks):raise HTTPException(400,'Manual privacy review is enabled. Complete it or turn it off in Settings.')
    done={}
    for previous in s.scalars(select(db.Run).where(db.Run.matter_id==mid,db.Run.purpose=='extraction',db.Run.provider==provider,db.Run.model==model)):
        done.update({(e['id'],e['revision']):e.get('hash') for e in previous.manifest if e.get('processed')})
    result=[]
    for c in chunks:
        digest=hashlib.sha256(c.text.encode()).hexdigest();old=done.get((c.id,c.revision));changed=(c.id,c.revision) in done and old and old!=digest
        if validate and changed:raise HTTPException(409,'Source text changed without a revision. Create a new matter version before continuing analysis.')
        result.append({'kind':'source','id':c.id,'revision':c.revision,'document_id':c.document_id,'hash':digest,'processed':(c.id,c.revision) in done and not changed})
    return result


class FullCorpusManager:
    def __init__(self,db):self.db=db;self.tasks={};self.events={}
    def dispatch(self,rid):
        if rid in self.tasks and not self.tasks[rid].done():return
        self.events[rid]=asyncio.Event();self.tasks[rid]=asyncio.create_task(self.run(rid),name='full-corpus-'+rid)
    def signal(self,rid):
        if rid in self.events:self.events[rid].set()
    async def start(self):
        with Session(self.db.engine) as s:
            ids=list(s.scalars(select(self.db.Run.id).where(self.db.Run.purpose=='full_corpus',self.db.Run.status=='running')))
        for rid in ids:self.dispatch(rid)
    async def close(self):
        tasks=[t for t in self.tasks.values() if not t.done()]
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
    @contextmanager
    def lock(self,mid):
        if self.db.engine.dialect.name!='postgresql':yield True;return
        key=int.from_bytes(hashlib.sha256(('full-corpus:'+mid).encode()).digest()[:8],'big',signed=True)
        with self.db.engine.connect() as c:
            acquired=c.scalar(text('SELECT pg_try_advisory_lock(:key)'),{'key':key})
            try:yield acquired
            finally:
                if acquired:c.execute(text('SELECT pg_advisory_unlock(:key)'),{'key':key})
    def recover_children(self,s,run):
        # Called only while holding the full-job advisory lock. No other process
        # can own this full job, and standalone runs are excluded by workspace guards.
        linked={a.details.get('run_id') for a in s.scalars(select(self.db.Audit).where(self.db.Audit.matter_id==run.matter_id)) if a.details.get('full_run_id')==run.id}
        for child in s.scalars(select(self.db.Run).where(self.db.Run.matter_id==run.matter_id,self.db.Run.id!=run.id,self.db.Run.status=='running')):
            if child.id in linked or any(e.get('full_run_id')==run.id for e in child.manifest):
                child.status='interrupted';child.error='Interrupted child workflow recovered by full-corpus job; saved checkpoints retained.'
                self.db.audit(s,run.matter_id,'full_analysis_child_recovered',{'full_run_id':run.id,'run_id':child.id})
        s.commit()
    def refresh_sources(self,s,run):
        current=source_scope(s,run.matter_id,run.provider,run.model)
        run.manifest=[control_of(run),*current,*[x for x in run.manifest if x.get('kind')=='witness']]
        return current
    async def run(self,rid):
        db=self.db
        with Session(db.engine) as s:
            parent=s.get(db.Run,rid)
            if not parent or parent.status!='running':return
            mid=parent.matter_id;provider=parent.provider;model=parent.model
        with self.lock(mid) as acquired:
            if not acquired:return
            parent_token=full_parent.set(rid);model_token=active_model.set(model)
            try:
                with Session(db.engine) as s:self.recover_children(s,s.get(db.Run,rid))
                while True:
                    try:
                        with Session(db.engine) as pre:
                            current=pre.get(db.Run,rid);saved=control_of(current) if current else {}
                            wait=0
                            if current and current.status=='running' and saved.get('phase')=='retry_wait' and saved.get('next_retry_at') and not saved.get('pause_requested') and not saved.get('cancel_requested'):
                                wait=max(0,(datetime.fromisoformat(saved['next_retry_at'])-datetime.now(timezone.utc)).total_seconds())
                        if wait:
                            self.events[rid].clear()
                            try:await asyncio.wait_for(self.events[rid].wait(),timeout=wait)
                            except asyncio.TimeoutError:pass
                            continue
                        with Session(db.engine) as s:
                            run=s.get(db.Run,rid)
                            if not run or run.status!='running':return
                            meta=control_of(run)
                            if meta.get('cancel_requested') or meta.get('pause_requested'):
                                run.status='cancelled' if meta.get('cancel_requested') else 'paused';update_control(run,phase=run.status)
                                db.audit(s,mid,'full_analysis_'+run.status,{'run_id':rid});s.commit();return
                            sources=self.refresh_sources(s,run);pending=[x for x in sources if not x['processed']]
                            if pending:update_control(run,phase='extraction');s.commit();action='extract'
                            else:
                                if not meta.get('draft_work_product'):
                                    run.status='completed';update_control(run,phase='completed');db.audit(s,mid,'full_analysis_completed',{'run_id':rid,'passages':len(sources),'synthesis_requested':False});s.commit();return
                                names=sorted({name for o in s.scalars(select(db.Output).where(db.Output.matter_id==mid,db.Output.kind.in_(['witness','event','relationship']),db.Output.review_status!='rejected')) for name in [o.payload.get('person'),o.payload.get('other_person')] if name})
                                finished={x['name'] for x in run.manifest if x.get('kind')=='witness' and x.get('processed')}
                                remaining=[name for name in names if name not in finished]
                                if not remaining:
                                    run.status='completed';update_control(run,phase='completed',witness_total=len(names));db.audit(s,mid,'full_analysis_completed',{'run_id':rid,'passages':len(sources),'witnesses':len(names),'synthesis_requested':True});s.commit();return
                                name=remaining[0];update_control(run,phase='witness_drafting',current_witness=name,witness_total=len(names));s.commit();action='draft'
                        if action=='extract':
                            await db.analyze(mid,db.ExtractionInput(provider=provider,passage_limit=meta.get('batch_size',25)))
                        else:
                            from .workproduct import WorkProductInput
                            settings=meta.get('question_settings',{}).get(name,{})
                            request={'witness':name,'provider':provider,'question_count':int(settings.get('questionCount',100)),'instructions':settings.get('topics','')}
                            for field,default in [('alignment','unknown'),('proceeding','deposition'),('examination','direct'),('style','open-ended')]:request[field]=settings.get(field,default)
                            result=await db.app.state.work_product_draft(mid,WorkProductInput(**request))
                            with Session(db.engine) as s:
                                run=s.get(db.Run,rid);run.manifest=[*run.manifest,{'kind':'witness','name':name,'processed':True,'work_product_run_id':result['run_id']}];s.commit()
                        with Session(db.engine) as s:
                            run=s.get(db.Run,rid);update_control(run,retry_count=0,next_retry_at=None);run.error='';s.commit()
                    except asyncio.CancelledError:raise
                    except Exception as error:
                        with Session(db.engine) as s:
                            run=s.get(db.Run,rid)
                            if not run:return
                            meta=control_of(run)
                            if meta.get('pause_requested') or meta.get('cancel_requested'):continue
                            message=error.detail if isinstance(error,HTTPException) and isinstance(error.detail,str) else 'Full-corpus workflow failed; completed checkpoints are retained.'
                            transient=any(word in message.lower() for word in ['temporarily','rate limit','quota','unable to reach','overloaded'])
                            count=meta.get('retry_count',0)
                            if transient and count<3:
                                delay=60*(2**count);when=datetime.now(timezone.utc)+timedelta(seconds=delay)
                                run.error=message;update_control(run,phase='retry_wait',retry_count=count+1,next_retry_at=when.isoformat());db.audit(s,mid,'full_analysis_retry',{'run_id':rid,'attempt':count+1,'delay_seconds':delay});s.commit()
                            else:
                                run.status='failed';run.error=message;update_control(run,phase='failed');db.audit(s,mid,'full_analysis_failed',{'run_id':rid,'error':message});s.commit();return
                        self.events[rid].clear()
                        try:await asyncio.wait_for(self.events[rid].wait(),timeout=delay)
                        except asyncio.TimeoutError:pass
            finally:active_model.reset(model_token);full_parent.reset(parent_token)


def register_full_corpus(app):
    from . import main as db
    class ModeInput(BaseModel): mode:Literal['full','manual']
    class ControlInput(BaseModel):
        action:Literal['pause','resume','cancel']
        run_id:str|None=None
    @app.post('/api/settings/analysis-mode')
    def mode(req:ModeInput):
        from dotenv import set_key
        try:set_key(str(db.ENV_PATH),'ANALYSIS_MODE',req.mode)
        except OSError:raise HTTPException(500,'Unable to save analysis mode') from None
        os.environ['ANALYSIS_MODE']=req.mode;return {'analysis_mode':req.mode}

    @app.post('/api/matters/{mid}/full-analysis/start')
    async def start_run(mid:str,req:FullAnalysisInput):
        with Session(db.engine) as s:
            db.matter(s,mid);s.execute(select(db.Matter).where(db.Matter.id==mid).with_for_update()).scalar_one()
            if s.scalar(select(db.Run.id).where(db.Run.matter_id==mid,db.Run.status.in_(ACTIVE_STATUSES)).limit(1)):raise HTTPException(409,'A workflow is already active; resume or cancel it before starting another.')
            from .workproduct import WorkProductInput
            if len(req.question_settings)>1000:raise HTTPException(400,'Too many witness question configurations')
            for name,settings in req.question_settings.items():
                try:WorkProductInput(witness=name,provider=req.provider,question_count=int(settings.get('questionCount',100)),alignment=settings.get('alignment','unknown'),proceeding=settings.get('proceeding','deposition'),examination=settings.get('examination','direct'),style=settings.get('style','open-ended'),instructions=settings.get('topics',''))
                except (ValueError,TypeError,AttributeError):raise HTTPException(400,'Invalid witness question settings') from None
            model=configured_model(req.provider);sources=source_scope(s,mid,req.provider,model)
            run=db.Run(matter_id=mid,provider=req.provider,model=model,purpose='full_corpus',prompt_version='full-corpus-v1',manifest=[{'kind':'control','phase':'extraction','batch_size':req.batch_size,'draft_work_product':req.draft_work_product,'question_settings':req.question_settings,'retry_count':0},*sources]);s.add(run);s.flush();rid=run.id
            db.audit(s,mid,'full_analysis_started',{'run_id':rid,'provider':req.provider,'model':model,'passages':len(sources),'documents':len({x['document_id'] for x in sources}),'draft_work_product':req.draft_work_product,'manual_privacy_review_required':db.privacy_review_required()});s.commit()
        app.state.full_corpus.dispatch(rid);return {'run_id':rid,'status':'running'}

    @app.get('/api/matters/{mid}/full-analysis/status')
    def status(mid:str):
        with Session(db.engine) as s:
            db.matter(s,mid);run=s.scalar(select(db.Run).where(db.Run.matter_id==mid,db.Run.purpose=='full_corpus').order_by(db.Run.created_at.desc()).limit(1))
            if not run:return {'status':'not_started','analysis_mode':analysis_mode()}
            sources=source_scope(s,mid,run.provider,run.model,validate=False);meta=control_of(run);done={x['id'] for x in sources if x['processed']};by_doc={}
            for item in sources:by_doc.setdefault(item['document_id'],[]).append(item['id'])
            return {'run_id':run.id,'status':run.status,'provider':run.provider,'model':run.model,'phase':meta.get('phase'),'processed_passages':len(done),'total_passages':len(sources),'completed_documents':sum(all(cid in done for cid in ids) for ids in by_doc.values()),'total_documents':len(by_doc),'draft_work_product':meta.get('draft_work_product'),'witnesses_completed':sum(x.get('kind')=='witness' and x.get('processed',False) for x in run.manifest),'witness_total':meta.get('witness_total',0),'current_witness':meta.get('current_witness',''),'retry_count':meta.get('retry_count',0),'next_retry_at':meta.get('next_retry_at'),'pause_requested':meta.get('pause_requested',False),'cancel_requested':meta.get('cancel_requested',False),'error':run.error}

    @app.post('/api/matters/{mid}/full-analysis/control')
    async def control(mid:str,req:ControlInput):
        with Session(db.engine) as s:
            db.matter(s,mid);run=s.scalar(select(db.Run).where(db.Run.matter_id==mid,db.Run.purpose=='full_corpus',*([db.Run.id==req.run_id] if req.run_id else [])).order_by(db.Run.created_at.desc()).with_for_update().limit(1))
            if not run:raise HTTPException(404,'No full-corpus run exists')
            if req.action=='resume':
                if run.status not in {'paused','failed'}:raise HTTPException(409,'This run is not paused or failed')
                other=s.scalar(select(db.Run.id).where(db.Run.matter_id==mid,db.Run.id!=run.id,db.Run.status.in_(ACTIVE_STATUSES)).limit(1))
                if other:raise HTTPException(409,'Another workflow is active')
                sources=source_scope(s,mid,run.provider,run.model);old={(x.get('id'),x.get('revision'),x.get('hash')) for x in run.manifest if x.get('kind')=='source'}
                changed=old!={(x['id'],x['revision'],x['hash']) for x in sources}
                witnesses=[] if changed else [x for x in run.manifest if x.get('kind')=='witness']
                run.manifest=[control_of(run),*sources,*witnesses];update_control(run,pause_requested=False,cancel_requested=False,retry_count=0,next_retry_at=None);run.status='running';run.error=''
            elif req.action=='pause':
                if run.status!='running':raise HTTPException(409,'This run is not running')
                update_control(run,pause_requested=True)
            else:
                if run.status not in {'running','paused','failed'}:raise HTTPException(409,'This run is already finished')
                update_control(run,cancel_requested=True)
                if run.status!='running':run.status='cancelled';update_control(run,phase='cancelled')
            rid=run.id;db.audit(s,mid,'full_analysis_control',{'run_id':rid,'action':req.action});s.commit()
        if req.action=='resume':app.state.full_corpus.dispatch(rid)
        app.state.full_corpus.signal(rid)
        return {'run_id':rid,'action':req.action,'message':'Control saved. In-flight work stops at the next passage or witness boundary.'}

    async def performance_control(mid,action,rid):
        return await control(mid,ControlInput(action=action,run_id=rid))
    app.state.performance_full_control=performance_control
