import sys
from pathlib import Path
import json
import pytest
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_limits import bound_text, jev_state, EMBED_QUERY_BYTES, JEV_STATE_BYTES
from test_incident import servers, add
from test_jev_runtime import backend
from test_serving import serving


def too_long():
    return httpx.HTTPStatusError('length', request=httpx.Request('POST','https://example.invalid'),
        response=httpx.Response(400, json={'error':{'code':'InvalidParameter',
        'message':'Range of input length should be [1, 8192]'}},
        request=httpx.Request('POST','https://example.invalid')))


@pytest.mark.parametrize('text',['abc','汉字'*10000,'🙂'*50000,'🧠\u0301abc汉字'*30000])
def test_utf8_bounds_no_broken_unicode(text):
    for n in (0,1,3,50,7680,30000):
        value=bound_text(text,n)
        assert len(value.encode('utf-8'))<=n
        assert '\ufffd' not in value
        if len(text.encode('utf-8'))<=n: assert value==text


@pytest.mark.parametrize('size',[62762,133904,185627])
def test_rejected_query_recovers_vector_then_jev_preserving_original(servers,size):
    s=servers[1]
    assert add(s,'alice',1)[0]==200
    s.EMBED_ENABLED=True
    s.rerank.ACTIVE,s.rerank.MODE=True,'jev'
    query='Orion HEAD '+('x'*(size-21))+' Orion END'
    assert len(query)==size
    submitted=[];rank_queries=[]
    def embed(texts):
        submitted.append(texts[0])
        if len(texts[0].encode())>EMBED_QUERY_BYTES: raise too_long()
        return [[1.,0.,0.]]
    s.embed=embed
    def rank(q,candidates,k):
        rank_queries.append(q)
        return [(mid,2.5) for mid,score,text in candidates],{'mode':'rerank'}
    s.rerank.apply=rank
    # Keep this test focused on routing: synthetic lexical candidate avoids
    # coupling expected results to the query's long dummy-word match.
    original=s.MemoryStore.recall
    s.MemoryStore.recall=lambda store,q,**kw: original(store,'Orion',**kw)
    try:
        code,payload=s.do_search({'query':query,'user_id':'alice','top_k':100})
    finally:s.MemoryStore.recall=original
    assert code==200 and payload['data']
    assert len(submitted)==2 and submitted[0]==query
    assert len(submitted[1].encode())<=EMBED_QUERY_BYTES
    assert submitted[1].startswith('Orion HEAD') and submitted[1].endswith('Orion END')
    assert rank_queries==[query]
    event=json.loads(s.LOG_PATH.read_text().splitlines()[-1])
    assert event['embedding']['mode']=='vector' and event['embedding']['query_truncated']
    assert event['rerank']['mode']=='rerank'
    assert query not in s.LOG_PATH.read_text()


def test_accepted_long_query_is_not_changed(servers):
    s=servers[1];seen=[]
    s.embed=lambda texts: seen.append(texts) or [[1.,2.]]
    q='a'*40000
    vector,meta=s.embed_query(q)
    assert seen==[[q]] and meta=={'mode':'vector'}


@pytest.mark.parametrize('status,message',[(400,'invalid dimensions'),(401,'Invalid API key'),(429,'rate limited'),(500,'input length')])
def test_non_length_rejections_not_hidden_or_retried(servers,status,message):
    s=servers[1];seen=[]
    def embed(texts):
        seen.append(texts)
        r=httpx.Response(status,json={'error':{'code':'InvalidParameter','message':message}},
                        request=httpx.Request('POST','https://example.invalid'))
        r.raise_for_status()
    s.embed=embed
    with pytest.raises(httpx.HTTPStatusError):s.embed_query('x'*185627)
    assert len(seen)==1


def test_short_query_and_memory_unchanged():
    assert jev_state('short query','original memory')=='Question: short query\nMemory: original memory'


@pytest.mark.parametrize('q,m',[('汉'*185627,'saved memory'),('x'*185627,'🙂'*20000),('short','x'*100000)])
def test_jev_complete_state_with_candidate_fits(q,m):
    result=jev_state(q,m)
    assert len(result.encode())<=JEV_STATE_BYTES
    assert result.startswith('Question: ') and '\nMemory: ' in result
    if len(m.encode())<20000: assert result.endswith(m)


def test_real_jev_transport_sends_bounded_state(backend):
    b=backend;sent=[]
    class Response:
        status_code=200;headers={}
        content=json.dumps({'model':b.JEV_MODEL,'answers':{'rel':{'score':3}},'usage':{'input_tokens':99}}).encode()
        def json(self):return {'model':b.JEV_MODEL,'answers':{'rel':{'score':3}},'usage':{'input_tokens':99}}
    def post(url,**kw):
        sent.append(kw['json'])
        return Response()
    b._session.post=post
    result=b._call(b.JEV_MODEL,'stub','x'*185627,'saved memory',1)
    assert result.score==3 and len(sent[0]['state'].encode())<=JEV_STATE_BYTES
    assert sent[0]['state'].endswith('saved memory')


@pytest.mark.parametrize('serving', [{'embedding': True}], indirect=True)
def test_long_query_full_local_http_chain(serving):
    request, state, env = serving
    q='Orion '+('word '*38000)+' Orion launch date?'
    code,headers,body=request('/v1/memories/add',{'request_id':'length-add','user_id':'length-user',
        'session_id':'s','messages':[{'role':'user','content':'Orion launch date is 2030-04-12.'}]})
    assert code==200
    code,headers,body=request('/v1/memories/search',{'query':q,'user_id':'length-user','top_k':100})
    assert code==200 and body['data']
    assert headers.get('X-AML-Rerank','').split(';')[0]=='rerank'
    assert headers['X-AML-Embedding'] == 'vector'
    assert state['requests'] and all(len(s.encode())<=JEV_STATE_BYTES for s in state['requests'])
