"""LlamaIndex-powered, local-only, workspace-scoped evidence retrieval."""
import hashlib
import json
import math
import os
from urllib.parse import urlparse
import httpx
from sqlalchemy import select
from .privacy import sanitize
from .providers import ProviderError

MODEL='nomic-embed-text:latest'
POLICY='masked-local-v1'
EMBED_WINDOW_CHARS=1024
WINDOW_POLICY='masked-local-llamaindex-char1024-overlap128-v2'

def known_names(s,mid):
    from .main import Output
    return sorted({name for o in s.scalars(select(Output).where(Output.matter_id==mid,Output.kind.in_(['witness','event','relationship']),Output.review_status!='rejected')) for name in [o.payload.get('person'),o.payload.get('other_person')] if name})

def masked(text,m,names):
    return sanitize(text,m.id,os.getenv('PRIVACY_KEY',''),[*m.sensitive_terms,*names])[0]

def text_fingerprint(text,revision):
    policy=WINDOW_POLICY if len(text)>EMBED_WINDOW_CHARS else POLICY
    return hashlib.sha256(json.dumps([policy,MODEL,revision,text],ensure_ascii=False).encode()).hexdigest()

def fingerprint(chunk,m,names):
    return text_fingerprint(masked(chunk.text,m,names),chunk.revision)

def pooled_vector(vectors,parts):
    weights=[max(1,len(part)) for part in parts];total=sum(weights)
    vector=[sum(row[index]*weight for row,weight in zip(vectors,weights))/total for index in range(768)]
    magnitude=math.sqrt(sum(value*value for value in vector))
    if not magnitude or not math.isfinite(magnitude):raise ValueError('Invalid pooled embedding')
    return [value/magnitude for value in vector]

async def embed(texts,query=False):
    from .llama_retrieval import embedding_windows
    url=os.getenv('OLLAMA_URL','http://127.0.0.1:11434').rstrip('/')
    parsed=urlparse(url)
    if parsed.scheme!='http' or parsed.hostname not in {'localhost','127.0.0.1','::1'}:
        raise ProviderError('Embeddings require a loopback Ollama URL; no remote embedding service is permitted.')
    prefix='search_query: ' if query else 'search_document: '
    try:
        async with httpx.AsyncClient(timeout=120,trust_env=False) as client:
            async def request(parts):
                response=await client.post(url+'/api/embed',json={'model':MODEL,'input':[prefix+part for part in parts],'truncate':False})
                if response.status_code in {400,413}:
                    try:message=str(response.json().get('error','')).lower()
                    except (ValueError,AttributeError):message=''
                    if any(term in message for term in ['context length','input length','too many tokens']):
                        # Never truncate a source to make indexing appear complete. Recurse
                        # only for an explicit context limit, preserving every text window.
                        result=[]
                        for part in parts:
                            if len(part)<=128:
                                if len(parts)==1:response.raise_for_status()
                                result.extend(await request([part]));continue
                            windows=embedding_windows(part,max_chars=max(128,len(part)//2))
                            result.append(pooled_vector(await request(windows),windows))
                        return result
                response.raise_for_status();vectors=response.json()['embeddings']
                if len(vectors)!=len(parts) or any(len(vector)!=768 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in vector) or not any(vector) for vector in vectors):raise ValueError()
                return vectors
            plans=[embedding_windows(text) for text in texts]
            flat=[part for plan in plans for part in plan];vectors=[]
            for offset in range(0,len(flat),32):vectors.extend(await request(flat[offset:offset+32]))
            result=[];offset=0
            for plan in plans:
                rows=vectors[offset:offset+len(plan)];offset+=len(plan)
                result.append(rows[0] if len(rows)==1 else pooled_vector(rows,plan))
            return result
    except (httpx.HTTPError,ValueError,KeyError,TypeError):
        raise ProviderError('Local embedding failed. Check Ollama and nomic-embed-text. No source text was sent to a cloud embedding API.') from None


def index_snapshot(s,m,limit=0):
    from .main import Chunk,Document,Embedding
    names=known_names(s,m.id)
    chunks=list(s.scalars(select(Chunk).join(Document).where(Document.matter_id==m.id,Document.status.notin_(['excluded','failed'])).order_by(Chunk.id)))
    existing={cid:(digest,model) for cid,digest,model in s.execute(select(Embedding.chunk_id,Embedding.fingerprint,Embedding.model).where(Embedding.matter_id==m.id))}
    pending=[];remaining=0
    for chunk in chunks:
        digest=fingerprint(chunk,m,names)
        if existing.get(chunk.id)!=(digest,MODEL):
            remaining+=1
            if len(pending)<limit:pending.append({'chunk_id':chunk.id,'document_id':chunk.document_id,'locator':chunk.locator,'revision':chunk.revision,'fingerprint':digest,'text':masked(chunk.text,m,names)})
    return {'pending':pending,'remaining':remaining,'current':len(chunks)-remaining,'total':len(chunks),'model':MODEL,'names_masked':len(names),'policy':POLICY,'long_passage_policy':WINDOW_POLICY,'retrieval_engine':'llamaindex'}


def store_vector(engine,mid,item,vector,actor='local-operator'):
    # Recheck after the HTTP call: deletion, exclusion, source edits or new masking
    # terms may have invalidated the snapshot while embedding was in flight.
    from sqlalchemy.orm import Session
    from .main import Matter,Chunk,Document,Embedding,workspace_deleted
    with Session(engine) as writer:
        m=writer.scalar(select(Matter).where(Matter.id==mid).with_for_update())
        if not m or workspace_deleted(writer,mid):return False
        chunk=writer.get(Chunk,item['chunk_id'])
        doc=writer.get(Document,chunk.document_id) if chunk else None
        if not doc or doc.matter_id!=mid or doc.status in {'excluded','failed'}:return False
        if fingerprint(chunk,m,known_names(writer,mid))!=item['fingerprint']:return False
        row=writer.scalar(select(Embedding).where(Embedding.chunk_id==chunk.id))
        if row is None:row=Embedding(matter_id=mid,chunk_id=chunk.id,created_by=actor);writer.add(row)
        row.model=MODEL;row.fingerprint=item['fingerprint'];row.vector=vector
        writer.commit();return True


async def build_index(s,m,limit=100,should_continue=None,on_progress=None,actor='local-operator'):
    import asyncio
    from sqlalchemy.orm import Session
    from .main import Matter,workspace_deleted
    engine=s.bind;mid=m.id
    def snapshot():
        with Session(engine) as reader:
            current=reader.get(Matter,mid)
            if not current or workspace_deleted(reader,mid):return {'pending':[],'remaining':0,'current':0,'total':0,'model':MODEL,'names_masked':0,'policy':POLICY,'deleted':True}
            return index_snapshot(reader,current,limit)
    initial=await asyncio.to_thread(snapshot)
    from .llama_retrieval import ScopedEvidenceStore,vector_index,source_node
    store=ScopedEvidenceStore(engine,mid,store_vector,should_continue=should_continue,actor=actor)
    index=vector_index(store,embed,MODEL)
    count=0;stopped=False
    for item in initial['pending']:
        if should_continue and not await should_continue():stopped=True;break
        node=source_node(mid,item['chunk_id'],item['document_id'],item['locator'],item['revision'],item['fingerprint'],item['text'])
        await index.ainsert_nodes([node])
        count=store.saved_count
        if store.stopped:stopped=True;break
        if on_progress:on_progress(count,initial)
    final=await asyncio.to_thread(snapshot)
    return {k:v for k,v in final.items() if k!='pending'}|{'indexed':count,'stopped':stopped}

async def search(s,m,query,limit=12,*,document_ids=None,approved_only=False):
    from .main import Chunk,Document,Embedding,Matter,workspace_deleted
    from sqlalchemy.orm import Session
    from .llama_retrieval import ScopedEvidenceStore,vector_index,source_node,WorkspaceBM25Retriever,AvailableSemanticRetriever,fuse,local_callbacks
    from llama_index.core.retrievers import VectorIndexRetriever
    if not 1<=limit<=100:raise ProviderError('Choose a retrieval limit between 1 and 100.','RETRIEVAL_QUERY_INVALID',400)
    if workspace_deleted(s,m.id):raise ProviderError('Workspace is in Trash.','SOURCE_SCOPE_INVALID',404)
    names=known_names(s,m.id);safe=masked(query,m,names)
    scope = [Document.matter_id == m.id, Document.status.notin_(['excluded', 'failed'])]
    if document_ids is not None:
        scope.append(Document.id.in_(document_ids))
    if approved_only:
        scope.append(Chunk.approved.is_(True))
    chunks = list(s.scalars(select(Chunk).join(Document).where(*scope).order_by(Chunk.id)))
    nodes={};texts={}
    for chunk in chunks:
        text=masked(chunk.text,m,names)
        digest=text_fingerprint(text,chunk.revision)
        nodes[chunk.id]=source_node(m.id,chunk.id,chunk.document_id,chunk.locator,chunk.revision,digest,text)
        texts[chunk.id]=chunk.text
    records=s.execute(select(Embedding.chunk_id,Embedding.fingerprint).where(Embedding.matter_id==m.id,Embedding.model==MODEL))
    valid={cid for cid,digest in records if cid in nodes and nodes[cid].metadata['fingerprint']==digest}
    lexical=WorkspaceBM25Retriever(nodes,texts,query,limit*3)
    semantic=None;warning=''
    if valid:
        store=ScopedEvidenceStore(s.bind,m.id,store_vector,nodes=nodes,valid=valid)
        index=vector_index(store,embed,MODEL)
        # Construct directly: from_vector_store has no in-memory node IDs; scope lives in SQL.
        semantic=AvailableSemanticRetriever(VectorIndexRetriever(index=index,similarity_top_k=limit*3,callback_manager=local_callbacks()))
    else:warning='No current embeddings available; keyword results only. Build the local index.'
    ranked=await fuse(lexical,semantic,safe,limit)
    if semantic and semantic.warning:warning=semantic.warning
    # A source or masking-policy change during the embedding HTTP call must not leak stale text.
    results=[];changed=False
    with Session(s.bind) as reader:
        current=reader.get(Matter,m.id)
        if not current or workspace_deleted(reader,m.id):raise ProviderError('Workspace is unavailable.','SOURCE_SCOPE_INVALID',404)
        current_names=known_names(reader,m.id)
        wanted={item.node.id_ for item in ranked}
        latest = {chunk.id: chunk for chunk in reader.scalars(
            select(Chunk).join(Document).where(*scope, Chunk.id.in_(wanted))
        )}
        for item in ranked:
            chunk=latest.get(item.node.id_)
            if not chunk or fingerprint(chunk,current,current_names)!=item.node.metadata['fingerprint']:
                changed=True;continue
            results.append({'chunk_id':chunk.id,'locator':chunk.locator,'document_id':chunk.document_id,'text':chunk.text,'score':float(item.score or 0)})
    if changed:warning=(warning+' ' if warning else '')+'Some sources changed during search. Retry to refresh the results.'
    return {'results':results,'indexed_current':len(valid),'total_passages':len(chunks),'warning':warning,
            'complete_corpus_review':False,'retrieval_engine':'llamaindex','retrieval_method':'bm25+pgvector+reciprocal-rank-fusion'}
