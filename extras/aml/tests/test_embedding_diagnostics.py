import io
import json
import types
import httpx
import pytest

from test_incident import servers, add


def response(status=400, code='InvalidParameter', message='Range of input length should be [1, 8192]'):
    return httpx.Response(status, json={'error':{'code':code,'message':message},
        'request_id':'12345678-1234-1234-1234-123456789abc'},
        request=httpx.Request('POST','https://example.invalid/embeddings'))


def test_provider_diagnostics_only_keep_operational_metadata(servers):
    server=servers[1]
    event=server.embedding_error_metadata(response(message='invalid input: private-query private-key'),['private-query'])
    assert event['provider_code']=='InvalidParameter'
    assert event['category']=='parameter' and event['max_chars']==13
    assert event['provider_request_id']=='12345678-1234-1234-1234-123456789abc'
    assert 'private' not in json.dumps(event)


def test_unknown_provider_code_and_request_id_not_logged(servers):
    server=servers[1]
    r=response(code='private-key')
    r._content=json.dumps({'error':{'code':'private-key','message':'private-query'},'request_id':'private-key'}).encode()
    event=server.embedding_error_metadata(r,['private-query'])
    assert event['provider_code']=='unrecognized' and 'provider_request_id' not in event
    assert 'private' not in json.dumps(event)


def test_add_embedding_failure_does_not_accept_unindexed_write(servers):
    server=servers[1];server.EMBED_ENABLED=True
    def fail(texts):server.embedding_raise_for_status(response(),texts)
    server.embed=fail
    with pytest.raises(httpx.HTTPStatusError):add(server,'alice',1)
    assert not server.db_path('alice').exists()


def test_logging_failure_preserves_upstream_exception(servers):
    server=servers[1]
    def fail(event):raise OSError('private log location')
    server.log=fail
    with pytest.raises(httpx.HTTPStatusError):server.embedding_raise_for_status(response(),['private-query'])
