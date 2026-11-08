"""LlamaIndex retrieval over the application's existing, workspace-scoped evidence.

No hosted parsers, default cloud models, secondary vector tables or source re-chunking.
Only masked source nodes enter the framework. Original text is hydrated after retrieval.
"""
import asyncio
import math
import re
from collections import Counter
from typing import Any

from pydantic import PrivateAttr
from sqlalchemy import select
from sqlalchemy.orm import Session
from llama_index.core import VectorStoreIndex
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.callbacks import CallbackManager
from llama_index.core.node_parser import TokenTextSplitter
from llama_index.core.llms import CustomLLM, LLMMetadata
from llama_index.core.retrievers import BaseRetriever, QueryFusionRetriever
from llama_index.core.retrievers.fusion_retriever import FUSION_MODES
from llama_index.core.schema import TextNode, NodeWithScore, QueryBundle, TransformComponent, NodeRelationship, RelatedNodeInfo
from llama_index.core.vector_stores.types import BasePydanticVectorStore, VectorStoreQuery, VectorStoreQueryResult, VectorStoreQueryMode

from .providers import ProviderError


def local_callbacks():
    manager=CallbackManager([])
    # LlamaIndex may inherit globally configured handlers even for an empty list.
    manager.set_handlers([])
    return manager


class PreserveSourcePassages(TransformComponent):
    """Existing chunk IDs, exact source text and locators are authoritative."""
    def __call__(self, nodes, **kwargs):
        return nodes


class RetrievalOnlyLLM(CustomLLM):
    """Fusion requires an LLM parameter even with num_queries=1. Deny generation."""
    @property
    def metadata(self):
        return LLMMetadata(model_name='retrieval-only', context_window=16384, num_output=1)

    def complete(self, prompt, formatted=False, **kwargs):
        raise ProviderError('Query rewriting and answer synthesis are disabled in retrieval.', 'RETRIEVAL_GENERATION_DISABLED')

    def stream_complete(self, prompt, formatted=False, **kwargs):
        raise ProviderError('Query rewriting and answer synthesis are disabled in retrieval.', 'RETRIEVAL_GENERATION_DISABLED')


class LocalOllamaEmbedding(BaseEmbedding):
    """Use the existing loopback-only, prefixed, dimension-validated embedding API."""
    _embed_call: Any = PrivateAttr()

    def __init__(self, embed_call, model):
        super().__init__(model_name=model, embed_batch_size=1, callback_manager=local_callbacks())
        self._embed_call = embed_call

    def _get_query_embedding(self, query):
        raise ProviderError('Use asynchronous local retrieval.', 'ASYNC_RETRIEVAL_REQUIRED')

    def _get_text_embedding(self, text):
        raise ProviderError('Use asynchronous local indexing.', 'ASYNC_RETRIEVAL_REQUIRED')

    async def _aget_query_embedding(self, query):
        return (await self._embed_call([query], query=True))[0]

    async def _aget_text_embedding(self, text):
        return (await self._embed_call([text]))[0]

    async def _aget_text_embeddings(self, texts):
        return await self._embed_call(texts)


def source_node(mid, chunk_id, document_id, locator, revision, digest, text):
    metadata = {'workspace_id': mid, 'chunk_id': chunk_id, 'document_id': document_id,
                'locator': locator, 'source_revision': revision, 'fingerprint': digest}
    return TextNode(id_=chunk_id, text=text, metadata=metadata,
                    excluded_embed_metadata_keys=list(metadata), excluded_llm_metadata_keys=list(metadata),
                    relationships={NodeRelationship.SOURCE: RelatedNodeInfo(node_id=document_id)})


class ScopedEvidenceStore(BasePydanticVectorStore):
    """Adapt existing corpus_embeddings. Workspace scope cannot be overridden by queries."""
    stores_text: bool = True
    _engine: Any = PrivateAttr()
    _mid: str = PrivateAttr()
    _nodes: dict = PrivateAttr(default_factory=dict)
    _valid: set = PrivateAttr(default_factory=set)
    _writer: Any = PrivateAttr()
    _should_continue: Any = PrivateAttr(default=None)
    _actor: str = PrivateAttr()
    _saved: set = PrivateAttr(default_factory=set)
    _stopped: bool = PrivateAttr(default=False)

    def __init__(self, engine, mid, writer, nodes=None, valid=None, should_continue=None, actor='local-operator'):
        super().__init__()
        self._engine, self._mid, self._writer = engine, mid, writer
        self._nodes, self._valid = nodes or {}, set(valid or ())
        self._should_continue, self._actor = should_continue, actor

    @property
    def client(self):
        return self._engine

    @property
    def saved_count(self):
        return len(self._saved)

    @property
    def stopped(self):
        return self._stopped

    def add(self, nodes, **kwargs):
        raise ProviderError('Use the guarded asynchronous indexing pipeline.', 'ASYNC_RETRIEVAL_REQUIRED')

    async def async_add(self, nodes, **kwargs):
        saved = []
        for node in nodes:
            if node.metadata.get('workspace_id') != self._mid or node.id_ != node.metadata.get('chunk_id'):
                raise ProviderError('Index node is outside this workspace.', 'SOURCE_SCOPE_INVALID')
            if self._should_continue and not await self._should_continue():
                self._stopped = True
                break
            from .retrieval import text_fingerprint
            digest=text_fingerprint(node.text,node.metadata.get('source_revision'))
            if digest!=node.metadata.get('fingerprint'):
                raise ProviderError('Index text differs from its source snapshot.','SOURCE_SNAPSHOT_INVALID')
            vector = node.get_embedding()
            if len(vector) != 768 or any(not math.isfinite(value) for value in vector) or not any(vector):
                raise ProviderError('Local embedding dimensions or values are invalid.', 'EMBEDDING_INVALID')
            item = {'chunk_id': node.id_, 'fingerprint': node.metadata['fingerprint']}
            if await asyncio.to_thread(self._writer, self._engine, self._mid, item, vector, self._actor):
                self._saved.add(node.id_); saved.append(node.id_)
        return saved

    def delete(self, ref_doc_id, **kwargs):
        # Deletion remains in the audited workspace/document lifecycle, not the framework.
        raise ProviderError('Use the application evidence and workspace controls for deletion.', 'SOURCE_LIFECYCLE_REQUIRED')

    def query(self, query: VectorStoreQuery, **kwargs):
        from . import main as db, retrieval
        if query.mode != VectorStoreQueryMode.DEFAULT or query.filters is not None:
            raise ProviderError('Unsupported vector query options.', 'RETRIEVAL_QUERY_INVALID')
        vector = query.query_embedding
        if vector is None or len(vector) != 768 or any(not math.isfinite(value) for value in vector) or not any(vector):
            raise ProviderError('Invalid local query embedding.', 'EMBEDDING_INVALID')
        allowed = self._valid & set(self._nodes)
        if query.node_ids is not None:
            allowed &= set(query.node_ids)
        if not allowed:
            return VectorStoreQueryResult(nodes=[], ids=[], similarities=[])
        with Session(self._engine) as session:
            db.matter(session, self._mid)
            base = select(db.Embedding).join(db.Chunk, db.Embedding.chunk_id == db.Chunk.id).join(db.Document, db.Chunk.document_id == db.Document.id).where(
                db.Embedding.matter_id == self._mid, db.Document.matter_id == self._mid,
                db.Document.status.notin_(['excluded', 'failed']), db.Embedding.model == retrieval.MODEL,
                db.Embedding.chunk_id.in_(allowed))
            if query.doc_ids is not None:
                base = base.where(db.Document.id.in_(query.doc_ids))
            if session.bind.dialect.name == 'postgresql':
                distance = db.Embedding.vector.cosine_distance(vector)
                statement = base.with_only_columns(db.Embedding.chunk_id, db.Embedding.fingerprint, distance).order_by(distance, db.Embedding.chunk_id).limit(query.similarity_top_k)
                ranked = [(cid, 1 - float(dist)) for cid, digest, dist in session.execute(statement) if digest == self._nodes[cid].metadata['fingerprint']]
            else:
                norm = lambda values: math.sqrt(sum(value * value for value in values))
                ranked = [(row.chunk_id, sum(a * b for a, b in zip(vector, row.vector)) / (norm(vector) * norm(row.vector)))
                          for row in session.scalars(base) if row.fingerprint == self._nodes[row.chunk_id].metadata['fingerprint']]
                ranked = sorted(ranked, key=lambda item: (-item[1], item[0]))[:query.similarity_top_k]
        return VectorStoreQueryResult(nodes=[self._nodes[cid].model_copy(deep=True) for cid, _ in ranked],
                                      ids=[cid for cid, _ in ranked], similarities=[score for _, score in ranked])


class WorkspaceBM25Retriever(BaseRetriever):
    """Local lexical ranking includes exact witness names without sending them to a model."""
    def __init__(self, nodes, texts, query, top_k):
        super().__init__(callback_manager=local_callbacks())
        self.nodes, self.top_k = nodes, top_k
        self.terms = list(dict.fromkeys(term for term in re.findall(r'\w+', query.casefold()) if len(term) > 2))[:30]
        self.documents = {cid: Counter(re.findall(r'\w+', text.casefold())) for cid, text in texts.items()}

    def _retrieve(self, query_bundle):
        count = len(self.documents)
        average = sum(sum(document.values()) for document in self.documents.values()) / count if count else 1
        frequencies = {term: sum(term in document for document in self.documents.values()) for term in self.terms}
        ranked = []
        for cid, document in self.documents.items():
            length, score = sum(document.values()), 0.0
            for term in self.terms:
                frequency = document.get(term, 0)
                if frequency:
                    idf = math.log(1 + (count - frequencies[term] + .5) / (frequencies[term] + .5))
                    score += idf * frequency * 2.2 / (frequency + 1.2 * (.25 + .75 * length / (average or 1)))
            if score > 0:
                ranked.append((score, cid))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [NodeWithScore(node=self.nodes[cid].model_copy(deep=True), score=score) for score, cid in ranked[:self.top_k]]


class AvailableSemanticRetriever(BaseRetriever):
    def __init__(self, retriever):
        super().__init__(callback_manager=local_callbacks())
        self.retriever, self.warning = retriever, ''

    def _retrieve(self, query_bundle):
        raise ProviderError('Use asynchronous local retrieval.', 'ASYNC_RETRIEVAL_REQUIRED')

    async def _aretrieve(self, query_bundle):
        try:
            return await self.retriever.aretrieve(query_bundle)
        except ProviderError:
            self.warning = 'Semantic search unavailable; keyword results only.'
            return []


def vector_index(store, embed_call, model):
    return VectorStoreIndex.from_vector_store(store, embed_model=LocalOllamaEmbedding(embed_call, model),
                                             callback_manager=local_callbacks(), transformations=[PreserveSourcePassages()],
                                             insert_batch_size=1, show_progress=False)


async def fuse(lexical, semantic, safe_query, limit):
    retrievers = [lexical] + ([semantic] if semantic is not None else [])
    fusion = QueryFusionRetriever(retrievers, llm=RetrievalOnlyLLM(callback_manager=local_callbacks()),
                                 num_queries=1, mode=FUSION_MODES.RECIPROCAL_RANK,
                                 similarity_top_k=limit, callback_manager=local_callbacks(), use_async=True)
    return await fusion.aretrieve(QueryBundle(query_str=safe_query))


def embedding_windows(text, max_chars=1024):
    if len(text)<=max_chars:return [text]
    splitter=TokenTextSplitter(chunk_size=max_chars,chunk_overlap=min(128,max_chars//8),
                              tokenizer=lambda value:[ord(char) for char in value],keep_whitespaces=True,
                              callback_manager=local_callbacks())
    return splitter.split_text(text)
