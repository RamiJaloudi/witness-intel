import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from llama_index.core import Settings, VectorStoreIndex
from llama_index.core.retrievers import QueryFusionRetriever, VectorIndexRetriever
from llama_index.core.schema import MetadataMode
from llama_index.core.vector_stores.types import VectorStoreQuery

from app import main, retrieval
from app.llama_retrieval import ScopedEvidenceStore, RetrievalOnlyLLM, WorkspaceBM25Retriever, source_node, fuse
from app.providers import ProviderError
from tests.test_workflow import client, create
from tests.test_workproduct import seed


def vector():
    return [1.0] + [0.0] * 767


def test_native_index_and_fusion_are_used_and_embeddings_are_compatible(client, monkeypatch):
    mid = create(client); chunk, _ = seed(client, mid)
    calls, text_inputs = [], []
    insert, fused, semantic = VectorStoreIndex.ainsert_nodes, QueryFusionRetriever.aretrieve, VectorIndexRetriever.aretrieve
    async def track_insert(self, nodes, **kwargs):
        calls.append('native_insert')
        assert nodes[0].node_id == chunk['id']
        assert nodes[0].get_content(MetadataMode.EMBED) == nodes[0].text
        return await insert(self, nodes, **kwargs)
    async def track_fusion(self, query):
        calls.append('native_fusion')
        assert self.num_queries == 1
        return await fused(self, query)
    async def track_semantic(self, query):
        calls.append('native_semantic')
        return await semantic(self, query)
    async def embed(texts, query=False):
        text_inputs.extend(texts)
        return [vector() for _ in texts]
    monkeypatch.setattr(VectorStoreIndex, 'ainsert_nodes', track_insert)
    monkeypatch.setattr(QueryFusionRetriever, 'aretrieve', track_fusion)
    monkeypatch.setattr(VectorIndexRetriever, 'aretrieve', track_semantic)
    monkeypatch.setattr(retrieval, 'embed', embed)
    assert client.post(f'/api/matters/{mid}/search/index', json={'limit':100}).json()['indexed'] == 1
    # No lexical match: this result must come from the native vector retriever.
    result = client.post(f'/api/matters/{mid}/search', json={'query':'unrelatedsearchtoken'}).json()
    assert result['results'][0]['chunk_id'] == chunk['id']
    assert result['retrieval_engine'] == 'llamaindex'
    assert {'native_insert', 'native_fusion', 'native_semantic'} <= set(calls)
    assert all('Alex' not in text and 'alex@example.com' not in text for text in text_inputs)
    with Session(main.engine) as session:
        row = session.scalar(select(main.Embedding).where(main.Embedding.chunk_id == chunk['id']))
        matter, source = session.get(main.Matter, mid), session.get(main.Chunk, chunk['id'])
        assert row.model == retrieval.MODEL
        assert row.fingerprint == retrieval.fingerprint(source, matter, retrieval.known_names(session, mid))


def test_no_default_cloud_models_are_resolved(client, monkeypatch):
    mid = create(client); seed(client, mid)
    async def embed(texts, query=False): return [vector() for _ in texts]
    monkeypatch.setattr(retrieval, 'embed', embed)
    def forbidden(*args, **kwargs): raise AssertionError('Framework default model resolution is forbidden')
    monkeypatch.setattr(type(Settings), 'llm', property(forbidden))
    monkeypatch.setattr(type(Settings), 'embed_model', property(forbidden))
    import llama_index.core
    from types import SimpleNamespace
    monkeypatch.setattr(llama_index.core,'global_handler',SimpleNamespace(event_starts_to_ignore=[],event_ends_to_ignore=[],on_event_start=forbidden,on_event_end=forbidden,start_trace=forbidden,end_trace=forbidden))
    assert client.post(f'/api/matters/{mid}/search/index', json={'limit':100}).status_code == 200
    assert client.post(f'/api/matters/{mid}/search', json={'query':'Alex meeting'}).status_code == 200
    with pytest.raises(ProviderError): RetrievalOnlyLLM().complete('Do not send this anywhere')


def test_remote_embedding_url_falls_back_to_local_lexical_search(client, monkeypatch):
    mid = create(client); chunk, _ = seed(client, mid)
    actual_embed = retrieval.embed
    async def fake_embed(texts, query=False): return [vector() for _ in texts]
    monkeypatch.setattr(retrieval, 'embed', fake_embed)
    client.post(f'/api/matters/{mid}/search/index', json={'limit':100})
    monkeypatch.setattr(retrieval, 'embed', actual_embed)
    monkeypatch.setenv('OLLAMA_URL', 'http://not-local.invalid:11434')
    def forbidden(*args, **kwargs): raise AssertionError('No network request is permitted')
    monkeypatch.setattr(retrieval.httpx, 'AsyncClient', forbidden)
    result = client.post(f'/api/matters/{mid}/search', json={'query':'Alex meeting'}).json()
    assert result['results'][0]['chunk_id'] == chunk['id']
    assert 'Semantic search unavailable' in result['warning']


def test_source_policy_changes_during_embedding_do_not_return_stale_text(client, monkeypatch):
    mid = create(client); seed(client, mid)
    async def fake_embed(texts, query=False):
        if query:
            with Session(main.engine) as session:
                matter = session.get(main.Matter, mid)
                matter.sensitive_terms = ['meeting']; session.commit()
        return [vector() for _ in texts]
    monkeypatch.setattr(retrieval, 'embed', fake_embed)
    client.post(f'/api/matters/{mid}/search/index', json={'limit':100})
    result = client.post(f'/api/matters/{mid}/search', json={'query':'meeting'}).json()
    assert result['results'] == []
    assert 'changed during search' in result['warning']


def test_node_scope_and_snapshot_are_checked_before_writing(client):
    mid = create(client); chunk, _ = seed(client, mid)
    store = ScopedEvidenceStore(main.engine, mid, retrieval.store_vector)
    node = source_node('foreign', chunk['id'], chunk['document_id'], 'text', 1, 'wrong', 'tampered')
    node.embedding = vector()
    with pytest.raises(ProviderError) as error: asyncio.run(store.async_add([node]))
    assert error.value.code == 'SOURCE_SCOPE_INVALID'
    node.metadata['workspace_id'] = mid
    with pytest.raises(ProviderError) as error: asyncio.run(store.async_add([node]))
    assert error.value.code == 'SOURCE_SNAPSHOT_INVALID'
    with Session(main.engine) as session:
        assert session.scalar(select(main.Embedding).where(main.Embedding.matter_id == mid)) is None


def test_bm25_prefers_query_coverage_over_one_repeated_word():
    texts = {'focused':'The support agreement was reviewed.', 'repetitive':'support ' * 1000}
    nodes = {cid:source_node('m',cid,cid,'text',1,'fingerprint',text) for cid,text in texts.items()}
    retriever = WorkspaceBM25Retriever(nodes,texts,'support agreement',2)
    assert retriever.retrieve('masked query')[0].node.node_id == 'focused'


def test_fusion_keeps_distinct_source_ids_for_identical_passages():
    texts = {'a':'meeting evidence', 'b':'meeting evidence'}
    nodes = {cid:source_node('m',cid,cid,'text',1,'fingerprint',text) for cid,text in texts.items()}
    first = WorkspaceBM25Retriever(nodes,texts,'meeting',2)
    second = WorkspaceBM25Retriever(nodes,texts,'evidence',2)
    result = asyncio.run(fuse(first,second,'masked',2))
    assert {item.node.node_id for item in result} == {'a','b'}


def test_settings_identify_the_framework(client):
    settings = client.get('/api/settings').json()
    assert settings['retrieval_engine'] == 'LlamaIndex'
    assert settings['retrieval_version'].startswith('0.14.')
    assert settings['default_provider'] == 'ollama'


def test_vector_store_scope_cannot_be_overridden_by_foreign_node_ids(client, monkeypatch):
    mid, other = create(client), create(client)
    own, _ = seed(client, mid); foreign, _ = seed(client, other)
    async def embed(texts, query=False): return [vector() for _ in texts]
    monkeypatch.setattr(retrieval, 'embed', embed)
    for workspace in [mid, other]:
        assert client.post(f'/api/matters/{workspace}/search/index',json={'limit':100}).status_code == 200
    with Session(main.engine) as session:
        row = session.scalar(select(main.Embedding).where(main.Embedding.chunk_id==foreign['id']))
        node = source_node(other,foreign['id'],foreign['document_id'],'text',1,row.fingerprint,'foreign content')
    store = ScopedEvidenceStore(main.engine,mid,retrieval.store_vector,nodes={foreign['id']:node},valid={foreign['id']})
    result = store.query(VectorStoreQuery(query_embedding=vector(),node_ids=[foreign['id']],similarity_top_k=10))
    assert result.ids == []


def test_long_embedding_uses_all_text_windows_and_preserves_short_inputs(monkeypatch):
    import json
    import hashlib
    import httpx
    requests=[];factory=httpx.AsyncClient
    def handle(request):
        body=json.loads(request.content);requests.append(body)
        assert body['truncate'] is False
        assert all(len(text.removeprefix('search_document: '))<=1024 for text in body['input'])
        return httpx.Response(200,json={'embeddings':[vector() for _ in body['input']]})
    monkeypatch.setattr(retrieval.httpx,'AsyncClient',lambda **kwargs:factory(transport=httpx.MockTransport(handle),**kwargs))
    monkeypatch.setenv('OLLAMA_URL','http://127.0.0.1:11434')
    text='START '+''.join(f'item-{index:04d} ' for index in range(500))+' END'
    result=asyncio.run(retrieval.embed(['short text',text]))
    assert len(result)==2 and all(len(row)==768 for row in result)
    parts=[part.removeprefix('search_document: ') for body in requests for part in body['input']]
    assert parts[0]=='short text' and any(part.startswith('START') for part in parts) and any(part.endswith('END') for part in parts)
    assert all(f'item-{index:04d}' in ''.join(parts) for index in range(500))
    assert retrieval.text_fingerprint('short text',1)==hashlib.sha256(json.dumps([retrieval.POLICY,retrieval.MODEL,1,'short text'],ensure_ascii=False).encode()).hexdigest()
    assert retrieval.text_fingerprint(text,1)!=hashlib.sha256(json.dumps([retrieval.POLICY,retrieval.MODEL,1,text],ensure_ascii=False).encode()).hexdigest()


def test_context_limit_retries_with_smaller_windows_instead_of_truncation(monkeypatch):
    import json
    import httpx
    factory=httpx.AsyncClient;accepted=[]
    def handle(request):
        body=json.loads(request.content);parts=[text.removeprefix('search_document: ') for text in body['input']]
        assert body['truncate'] is False
        if any(len(part)>300 for part in parts):
            return httpx.Response(400,json={'error':'input length exceeds context length'})
        accepted.extend(parts)
        return httpx.Response(200,json={'embeddings':[vector() for _ in parts]})
    monkeypatch.setattr(retrieval.httpx,'AsyncClient',lambda **kwargs:factory(transport=httpx.MockTransport(handle),**kwargs))
    monkeypatch.setenv('OLLAMA_URL','http://127.0.0.1:11434')
    text='BEGIN '+''.join(f'record-{index:04d} ' for index in range(250))+' FINISH'
    result=asyncio.run(retrieval.embed([text]))
    assert len(result[0])==768
    assert any(part.startswith('BEGIN') for part in accepted) and any(part.endswith('FINISH') for part in accepted)
    assert all(f'record-{index:04d}' in ''.join(accepted) for index in range(250))
