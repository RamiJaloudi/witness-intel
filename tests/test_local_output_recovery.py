import asyncio
import json

import httpx
import pytest

from app import providers
from app.evidence import Findings


def local_client(monkeypatch,respond):
    original=httpx.AsyncClient
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(respond),**kwargs))
    monkeypatch.setenv('OLLAMA_MODEL','local-test-model')


def test_invalid_local_response_regenerates_same_model_without_accepting_bad_items(monkeypatch):
    calls=[]
    def respond(request):
        body=json.loads(request.content);calls.append(body)
        assert body['model']=='local-test-model'
        if len(calls)==1:
            monkeypatch.setenv('OLLAMA_MODEL','changed-during-run')
            return httpx.Response(200,json={'message':{'content':'{"findings":null}'},'done_reason':'stop'})
        assert 'OUTPUT FORMAT RETRY' in body['messages'][1]['content']
        assert body['options']['num_predict']==8192
        return httpx.Response(200,json={'message':{'content':'{"findings":[]}'},'done_reason':'stop'})
    local_client(monkeypatch,respond)
    result,model=asyncio.run(providers.generate('ollama','Synthetic evidence only',Findings))
    assert result.findings==[] and model=='local-test-model' and len(calls)==2
    assert providers.active_model.get() is None


@pytest.mark.parametrize('content,reason',[
    ('{"findings":null}','stop'),
    ('{"findings":[]}','length'),
    ('','stop'),
])
def test_local_output_recovery_is_bounded_and_never_accepts_truncated_json(monkeypatch,content,reason):
    calls=[]
    def respond(request):
        calls.append(request)
        return httpx.Response(200,json={'message':{'content':content},'done_reason':reason})
    local_client(monkeypatch,respond)
    with pytest.raises(providers.ProviderError):
        asyncio.run(providers.generate('ollama','Synthetic evidence only',Findings))
    assert len(calls)==3 and providers.active_model.get() is None
