import asyncio
import os
from typing import Literal
from contextvars import ContextVar, Token
import httpx
from pydantic import ValidationError
from .evidence import SYSTEM, Findings

DEFAULT_GEMINI_MODEL = 'gemini-3.8-flash'
Provider = Literal['gemini', 'ollama', 'openai', 'anthropic']
CLOUD_PROVIDERS = {'gemini', 'openai', 'anthropic'}
PROVIDERS = {
    'gemini': {'name': 'Gemini', 'model_env': 'GEMINI_MODEL', 'key_env': 'GEMINI_API_KEY', 'default_model': DEFAULT_GEMINI_MODEL},
    'openai': {'name': 'OpenAI (ChatGPT models)', 'model_env': 'OPENAI_MODEL', 'key_env': 'OPENAI_API_KEY', 'default_model': 'gpt-4.1'},
    'anthropic': {'name': 'Anthropic Claude', 'model_env': 'ANTHROPIC_MODEL', 'key_env': 'ANTHROPIC_API_KEY', 'default_model': 'claude-sonnet-5-5'},
    'ollama': {'name': 'Ollama', 'model_env': 'OLLAMA_MODEL', 'default_model': 'qwen2.5:latest'},
}
RETRY_DELAYS = (1, 2)
active_model = ContextVar('active_generation_model', default=None)

class ProviderError(Exception):
    """Application-authored messages only; never raw provider text."""
    def __init__(self, message, code='PROVIDER_ERROR', status=502):
        super().__init__(message); self.code=code; self.status=status

def configured_model(provider):
    if provider not in PROVIDERS: raise ProviderError('Choose a supported provider in Settings.', 'INVALID_PROVIDER', 400)
    if active_model.get(): return active_model.get()
    config=PROVIDERS[provider]
    return os.getenv(config['model_env'], config['default_model'])


def structured_schema(schema):
    """Provider-compatible schema; original Pydantic constraints still validate outputs."""
    result=schema.model_json_schema()
    constraints={'minLength','maxLength','minimum','maximum','exclusiveMinimum','exclusiveMaximum','multipleOf','minItems','maxItems','pattern','format'}
    def visit(node):
        if isinstance(node,list):
            for value in node:visit(value)
        elif isinstance(node,dict):
            removed={key:node.pop(key) for key in list(node) if key in constraints}
            if removed:node['description']=(node.get('description','')+' Required constraints: '+str(removed)).strip()
            node.pop('default',None)
            if node.get('type')=='object':
                node['additionalProperties']=False
                node['required']=list(node.get('properties',{}))
            for value in node.values():visit(value)
    visit(result)
    return result

def response_error(provider, status):
    name=PROVIDERS.get(provider,{}).get('name','Selected provider')
    if status in {401,403}: return ProviderError(f'{name} rejected authentication or permissions (HTTP {status}). Check the API key and project access in Settings.','AUTHENTICATION',status)
    if status==404: return ProviderError(f'{name} model is unavailable for this account (HTTP 404). Choose an available model in Settings and use Test connection.','MODEL_UNAVAILABLE',status)
    if status==429: return ProviderError(f'{name} quota or rate limit reached (HTTP 429). Check the account quota or wait before retrying. Verified completed passages are retained.','RATE_LIMIT',status)
    if status in {500,502,503,504,529}: return ProviderError(f'{name} is temporarily unavailable or overloaded (HTTP {status}) after bounded retries. Retry later or explicitly select another configured provider.','PROVIDER_UNAVAILABLE',status)
    return ProviderError(f'{name} rejected the request (HTTP {status}). Check the model and API configuration; source content was not included in this error.','REQUEST_REJECTED',status)

async def generate(provider, prompt, schema):
    """Retry malformed local responses without accepting or repairing their contents."""
    from . import job_runtime
    await job_runtime.boundary()
    token=active_model.set(configured_model(provider))
    measurement_token: Token | None = job_runtime.begin_measurement(provider,configured_model(provider))
    measurement_error=None
    try:
        for attempt in range(3 if provider=='ollama' else 1):
            try:
                await job_runtime.boundary()
                result=await _generate_once(provider,prompt,schema,output_limit=8192)
                job_runtime.finish_measurement(measurement_token)
                measurement_token=None
                await job_runtime.boundary()
                return result
            except ProviderError as error:
                if provider!='ollama' or error.code not in {'INVALID_STRUCTURED_OUTPUT','TRUNCATED_OUTPUT','EMPTY_RESPONSE'} or attempt==2:
                    raise
                # Regenerate against the original evidence, never coerce an invalid response
                # into findings or mark the source processed simply to move past it.
                prompt=prompt+'\n\nOUTPUT FORMAT RETRY: Return one complete JSON object matching the requested schema. '
                prompt+='Use only the schema enum values and correct field types. Unknown strings must be empty strings, not null. '
                prompt+='Keep findings and quotations concise; do not repeat entire source documents. Return an empty list only when no supported items exist.'
    except BaseException as error:
        measurement_error=error
        raise
    finally:
        if measurement_token is not None:job_runtime.finish_measurement(measurement_token,measurement_error)
        active_model.reset(token)


async def _generate_once(provider, prompt, schema, output_limit=8192):
    model=configured_model(provider)
    async with httpx.AsyncClient(timeout=180,trust_env=False) as client:
        if provider=='gemini':
            key=os.getenv('GEMINI_API_KEY','')
            if not key: raise ProviderError('GEMINI_API_KEY is missing. Add a key in Settings or explicitly select Ollama.','MISSING_KEY',401)
            url=f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
            headers={'x-goog-api-key':key}
            payload={'system_instruction':{'parts':[{'text':SYSTEM}]},'contents':[{'parts':[{'text':prompt}]}],
                'generationConfig':{'temperature':0,'responseMimeType':'application/json','responseJsonSchema':schema.model_json_schema()}}
        elif provider in {'openai','anthropic'}:
            config=PROVIDERS[provider];key=os.getenv(config['key_env'],'')
            if not key:raise ProviderError(f'{config["name"]} API key is missing. Add a key in Settings.', 'MISSING_KEY',401)
            if provider=='openai':
                url='https://api.openai.com/v1/responses'
                headers={'Authorization':'Bearer '+key}
                payload={'model':model,'store':False,'instructions':SYSTEM,'input':prompt,'max_output_tokens':8192,
                         'text':{'format':{'type':'json_schema','name':schema.__name__,'strict':True,'schema':structured_schema(schema)}}}
            else:
                url='https://api.anthropic.com/v1/messages'
                headers={'x-api-key':key,'anthropic-version':'2023-06-01'}
                payload={'model':model,'max_tokens':8192,'system':SYSTEM,'messages':[{'role':'user','content':prompt}],
                         'output_config':{'format':{'type':'json_schema','schema':structured_schema(schema)}}}
        elif provider=='ollama':
            url=os.getenv('OLLAMA_URL','http://127.0.0.1:11434')+'/api/chat';headers={}
            local_system=SYSTEM
            if schema is Findings:
                local_system+='\nKeep each finding to one concise supported statement and the shortest exact quote that supports it (at least 8 characters). Never copy the entire passage into each quote. Do not repeat identical findings. Unknown string fields must be empty strings, not null.'
            payload={'model':model,'stream':False,'format':schema.model_json_schema(),'messages':[{'role':'system','content':local_system},{'role':'user','content':prompt}],'options':{'temperature':0,'num_ctx':16384,'num_predict':output_limit}}
            if model.lower().startswith('qwen3.5:'):payload['think']=False
        for attempt in range(3):
            from .job_runtime import request_attempt
            request_attempt()
            try: response=await client.post(url,headers=headers,json=payload)
            except httpx.RequestError:
                if attempt<2: await asyncio.sleep(RETRY_DELAYS[attempt]);continue
                raise ProviderError(f'Unable to reach {provider}. Check connectivity and service availability.','CONNECTION_FAILED') from None
            if response.status_code in {429,500,502,503,504,529} and attempt<2:
                await asyncio.sleep(RETRY_DELAYS[attempt]);continue
            if response.status_code!=200: raise response_error(provider,response.status_code)
            break
        try:
            data=response.json()
            from .job_runtime import capture_usage
            capture_usage(provider,data)
            if provider=='gemini':
                candidate=data['candidates'][0]
                if candidate.get('finishReason')=='MAX_TOKENS': raise ProviderError('Gemini output was truncated. This passage was not accepted; retry with a suitable model.','TRUNCATED_OUTPUT')
                content=''.join(p.get('text','') for p in candidate['content']['parts'] if not p.get('thought'))
            elif provider=='openai':
                if data.get('status')=='incomplete':raise ProviderError('OpenAI output was incomplete. No findings were accepted. Retry with a suitable model.', 'TRUNCATED_OUTPUT')
                blocks=[block for item in data.get('output',[]) if item.get('type')=='message' for block in item.get('content',[])]
                if any(b.get('type')=='refusal' for b in blocks):raise ProviderError('OpenAI declined this request. No findings were accepted.', 'REFUSAL')
                content=''.join(b.get('text','') for b in blocks if b.get('type')=='output_text')
            elif provider=='anthropic':
                if data.get('stop_reason')=='max_tokens':raise ProviderError('Claude output was truncated. No findings were accepted. Retry with a suitable model.', 'TRUNCATED_OUTPUT')
                if data.get('stop_reason')=='refusal':raise ProviderError('Claude declined this request. No findings were accepted.', 'REFUSAL')
                content=''.join(b.get('text','') for b in data['content'] if b.get('type')=='text')
            else:
                if data.get('done_reason')=='length':raise ProviderError('Ollama output was truncated. This passage was not accepted; completed passage checkpoints are retained.', 'TRUNCATED_OUTPUT')
                content=data['message']['content']
            if not content:raise ProviderError('The provider returned no usable structured response. No findings were accepted.', 'EMPTY_RESPONSE')
        except (KeyError,IndexError,ValueError,TypeError):
            raise ProviderError(f'{provider} returned no usable structured response. No findings were accepted for this passage.','EMPTY_RESPONSE') from None
    try: return schema.model_validate_json(content),model
    except ValidationError:
        raise ProviderError('The model returned invalid structured output. No findings were accepted for this passage.','INVALID_STRUCTURED_OUTPUT') from None

async def gemini_models():
    key=os.getenv('GEMINI_API_KEY','')
    if not key: raise ProviderError('Add a Gemini API key in Settings first.','MISSING_KEY',401)
    try:
        async with httpx.AsyncClient(timeout=30,trust_env=False) as client:
            response=await client.get('https://generativelanguage.googleapis.com/v1beta/models',headers={'x-goog-api-key':key})
    except httpx.RequestError: raise ProviderError('Unable to reach the Gemini model catalog.','CONNECTION_FAILED') from None
    if response.status_code!=200: raise response_error('gemini',response.status_code)
    try:
        return [m['name'].removeprefix('models/') for m in response.json().get('models',[]) if 'generateContent' in m.get('supportedGenerationMethods',[]) and not any(t in m['name'] for t in ['image','tts','embedding','live','robotics'])]
    except (KeyError,ValueError): raise ProviderError('Gemini returned an invalid model catalog.','INVALID_RESPONSE') from None

async def cloud_models(provider):
    if provider=='gemini':return await gemini_models()
    if provider not in {'openai','anthropic'}:raise ProviderError('Choose a supported cloud provider.', 'INVALID_PROVIDER',400)
    config=PROVIDERS[provider];key=os.getenv(config['key_env'],'')
    if not key:raise ProviderError(f'Add a {config["name"]} API key in Settings first.', 'MISSING_KEY',401)
    headers={'Authorization':'Bearer '+key} if provider=='openai' else {'x-api-key':key,'anthropic-version':'2023-06-01'}
    url='https://api.openai.com/v1/models' if provider=='openai' else 'https://api.anthropic.com/v1/models'
    models=[];params={}
    try:
        async with httpx.AsyncClient(timeout=30,trust_env=False) as client:
            while True:
                response=await client.get(url,headers=headers,params=params)
                if response.status_code!=200:raise response_error(provider,response.status_code)
                data=response.json();models.extend(item['id'] for item in data['data'])
                if provider!='anthropic' or not data.get('has_more'):break
                cursor=data['last_id']
                if not cursor or cursor==params.get('after_id'):raise ValueError('Invalid pagination')
                params={'after_id':cursor}
        if provider=='openai':
            models=[m for m in models if m.startswith(('gpt-','o1','o3','o4')) and not any(t in m for t in ('audio','realtime','image','transcribe','tts','search','instruct'))]
        return sorted(set(models))
    except httpx.RequestError:raise ProviderError('Unable to reach the provider model catalog.', 'CONNECTION_FAILED') from None
    except (KeyError,ValueError,TypeError):raise ProviderError('The provider returned an invalid model catalog.', 'INVALID_RESPONSE') from None


async def test_connection(provider='gemini'):
    parsed,model=await generate(provider,'Synthetic connection test only. Extract the person and supported fact from: "Fictional Alex Morgan chaired the planning meeting." Use chunk_id "synthetic-connection-test". Do not access any workspace.',Findings)
    return {'ok':True,'model':model,'synthetic_findings':len(parsed.findings),'workspace_documents_sent':False}
