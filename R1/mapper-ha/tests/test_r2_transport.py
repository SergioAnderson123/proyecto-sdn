import asyncio
from unittest.mock import AsyncMock
import httpx
import pytest
from r2_transport import post_mab

def test_connection_failure_uses_second_replica():
    response = httpx.Response(403, json={"reason": "PORT_NOT_ALLOWED"})
    client = AsyncMock()
    client.post.side_effect = [httpx.ConnectError("down"), response]
    assert asyncio.run(post_mab(client, ["http://one", "http://two"], {})) is response
    assert client.post.call_args.args[0] == "http://two/api/mab-request"

@pytest.mark.parametrize("failure", [httpx.ReadTimeout("unknown"), httpx.RemoteProtocolError("unknown")])
def test_ambiguous_failure_is_not_replayed(failure):
    client = AsyncMock()
    client.post.side_effect = failure
    with pytest.raises(type(failure)):
        asyncio.run(post_mab(client, ["http://one", "http://two"], {}))
    assert client.post.call_count == 1

def test_denial_is_not_retried():
    client = AsyncMock()
    client.post.return_value = httpx.Response(403)
    asyncio.run(post_mab(client, ["http://one", "http://two"], {}))
    assert client.post.call_count == 1
