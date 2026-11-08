import asyncio
import json

import httpx
import pytest

from app import providers
from app.evidence import Findings
from app.workproduct import Narrative,Issues
from app.question_context import AttorneyEdits


@pytest.mark.parametrize('provider',['openai','anthropic'])
def test_cloud_request_schema_and_response(monkeypatch,provider):
    original=httpx.AsyncClient;requests=[]
    key='synthetic-provider-key-for-test'
    monkeypatch.setenv(providers.PROVIDERS[provider]['key_env'],key)
    def respond(request):
        requests.append(request)
        body=json.loads(request.content)
        assert body['model']==providers.configured_model(provider)
        if provider=='openai':
            assert request.headers['authorization']=='Bearer '+key
            assert request.url==httpx.URL('https://api.openai.com/v1/responses')
            assert body['store'] is False and body['text']['format']['strict']
            schema=body['text']['format']['schema']
            data={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'{"findings":[]}'}]}]}
        else:
            assert request.headers['x-api-key']==key
            assert request.url==httpx.URL('https://api.anthropic.com/v1/messages')
            schema=body['output_config']['format']['schema']
            data={'stop_reason':'end_turn','content':[{'type':'text','text':'{"findings":[]}'}]}
        finding=schema['$defs']['Finding']
        assert finding['additionalProperties'] is False
        assert set(finding['required'])==set(finding['properties'])
        return httpx.Response(200,json=data)
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(respond),**kwargs))
    result,_=asyncio.run(providers.generate(provider,'Synthetic test',Findings))
    assert result.findings==[] and len(requests)==1


@pytest.mark.parametrize('schema',[Findings,Narrative,Issues,AttorneyEdits])
def test_cloud_schema_constraints_remain_locally_enforced(schema):
    original=schema.model_json_schema()
    output=providers.structured_schema(schema)
    assert schema.model_json_schema()==original
    assert output['additionalProperties'] is False
    def check(node):
        if isinstance(node,dict):
            assert not {'minLength','maxLength','minItems','maxItems','default'} & node.keys()
            if node.get('type')=='object':
                assert node['additionalProperties'] is False
                assert set(node['required'])==set(node['properties'])
            for value in node.values():check(value)
        elif isinstance(node,list):
            for value in node:check(value)
    check(output)


@pytest.mark.parametrize('provider',['openai','anthropic'])
@pytest.mark.parametrize('case',['authentication','truncated','refusal','invalid','overloaded'])
def test_cloud_errors_do_not_accept_partial_or_expose_provider_text(monkeypatch,provider,case):
    original=httpx.AsyncClient;calls=[]
    monkeypatch.setenv(providers.PROVIDERS[provider]['key_env'],'synthetic-test-key')
    monkeypatch.setattr(providers,'RETRY_DELAYS',(0,0))
    def respond(request):
        calls.append(request)
        if case in {'authentication','overloaded'}:
            return httpx.Response(401 if case=='authentication' else 529,json={'error':'PRIVATE_PROVIDER_TEXT'})
        if provider=='openai':
            data={'status':'incomplete' if case=='truncated' else 'completed','output':[{'type':'message','content':[{'type':'refusal' if case=='refusal' else 'output_text','text':'PRIVATE_PROVIDER_TEXT'}]}]}
        else:
            data={'stop_reason':{'truncated':'max_tokens','refusal':'refusal'}.get(case,'end_turn'),'content':[{'type':'text','text':'PRIVATE_PROVIDER_TEXT'}]}
        return httpx.Response(200,json=data)
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(respond),**kwargs))
    with pytest.raises(providers.ProviderError) as error:
        asyncio.run(providers.generate(provider,'Synthetic test',Findings))
    assert 'PRIVATE_PROVIDER_TEXT' not in str(error.value)
    assert len(calls)==(3 if case=='overloaded' else 1)


def test_anthropic_model_catalog_pagination(monkeypatch):
    original=httpx.AsyncClient;calls=[]
    monkeypatch.setenv('ANTHROPIC_API_KEY','synthetic-test-key')
    def respond(request):
        calls.append(request)
        if len(calls)==1:
            return httpx.Response(200,json={'data':[{'id':'claude-first'}],'has_more':True,'last_id':'claude-first'})
        assert request.url.params['after_id']=='claude-first'
        return httpx.Response(200,json={'data':[{'id':'claude-second'}],'has_more':False})
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(respond),**kwargs))
    assert asyncio.run(providers.cloud_models('anthropic'))==['claude-first','claude-second']


def test_unknown_provider_never_falls_back():
    with pytest.raises(providers.ProviderError,match='supported provider'):
        asyncio.run(providers.generate('unknown','Synthetic test',Findings))
