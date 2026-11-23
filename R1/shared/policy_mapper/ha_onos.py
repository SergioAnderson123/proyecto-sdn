"""ONOS failover transport. Never blindly replay a possibly accepted POST."""
import os
import time
from urllib.parse import urlsplit
import httpx
import ha_store

_Original=httpx.AsyncClient
ENDPOINTS=[s.rstrip('/') for s in os.getenv('ONOS_ENDPOINTS','').split(',') if s]
SOURCE=os.getenv('ONOS_HOST','onos_controller')
MASTER_TTL_S=max(0.0, float(os.getenv('ONOS_MASTER_CACHE_TTL_S','5')))
MASTER_TIMEOUT_S=max(0.1, float(os.getenv('ONOS_MASTER_TIMEOUT_S','1')))
_MASTER_CACHE={}


def _flow_device(path):
    """Return the device ID for a per-device ONOS flow endpoint."""
    parts=urlsplit(path).path.split('/')
    if len(parts)>=5 and parts[1:4]==['onos','v1','flows']:
        return parts[4] or None
    return None

class AsyncClient(_Original):
    async def _master_index(self, dpid, kwargs):
        key=(tuple(ENDPOINTS),dpid)
        cached=_MASTER_CACHE.get(key)
        now=time.monotonic()
        if cached and now-cached[1] < MASTER_TTL_S:
            return cached[0]
        auth=kwargs.get('auth')
        for endpoint in ENDPOINTS:
            try:
                response=await super().request(
                    'GET', endpoint+'/onos/v1/mastership/'+dpid+'/master',
                    auth=auth, timeout=MASTER_TIMEOUT_S,
                )
                if response.status_code != 200:
                    continue
                node_id=response.json().get('nodeId')
                for index, candidate in enumerate(ENDPOINTS):
                    if urlsplit(candidate).hostname == node_id:
                        _MASTER_CACHE[key]=(index,now)
                        return index
            except (httpx.HTTPError, ValueError, TypeError):
                continue
        _MASTER_CACHE[key]=(None,now)
        return None

    async def request(self,method,url,**kwargs):
        u=urlsplit(str(url))
        if not ENDPOINTS or not u.path.startswith('/onos/') or u.hostname not in {SOURCE,*[urlsplit(e).hostname for e in ENDPOINTS]}:
            return await super().request(method,url,**kwargs)
        mutating=method.upper() not in ('GET','HEAD','OPTIONS')
        last=None
        order=list(range(len(ENDPOINTS)))
        dpid=_flow_device(u.path)
        master=await self._master_index(dpid,kwargs) if dpid else None
        if master is not None:
            order=[master]+[idx for idx in order if idx != master]
        for index in order:
            endpoint=ENDPOINTS[index]
            if mutating: ha_store.fence()
            target=endpoint+u.path+('?' +u.query if u.query else '')
            try:
                response=await super().request(method,target,**kwargs)
            except (httpx.ConnectError,httpx.ConnectTimeout) as e:
                last=e
                if index == master:
                    _MASTER_CACHE.pop((tuple(ENDPOINTS),dpid),None)
                continue
            except (httpx.ReadError,httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                if mutating: raise
                last=e
                continue
            # A GET can use another replica if one REST application is still starting.
            if not mutating and response.status_code in (404,502,503,504):
                last=response
                continue
            return response
        if isinstance(last,httpx.Response): return last
        if last: raise last
        raise ha_store.Unavailable('No ONOS endpoint available')
