"""Fail over only before connection; never replay an ambiguous MAB POST."""
import httpx

async def post_mab(client, endpoints, payload):
    if not endpoints:
        raise httpx.ConnectError("No R2 endpoints configured")
    for index, endpoint in enumerate(endpoints):
        try:
            return await client.post(endpoint + "/api/mab-request", json=payload,
                                     timeout=httpx.Timeout(8, connect=1))
        except (httpx.ConnectError, httpx.ConnectTimeout):
            if index == len(endpoints) - 1:
                raise
