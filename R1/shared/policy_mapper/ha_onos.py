"""ONOS failover transport. Never blindly replay a possibly accepted POST."""
import os
from urllib.parse import urlsplit
import httpx
import ha_store

_Original=httpx.AsyncClient
ENDPOINTS=[s.rstrip('/') for s in os.getenv('ONOS_ENDPOINTS','').split(',') if s]
SOURCE=os.getenv('ONOS_HOST','onos_controller')

class AsyncClient(_Original):
    async def request(self,method,url,**kwargs):
        u=urlsplit(str(url))
        if not ENDPOINTS or not u.path.startswith('/onos/') or u.hostname not in {SOURCE,*[urlsplit(e).hostname for e in ENDPOINTS]}:
            return await super().request(method,url,**kwargs)
        mutating=method.upper() not in ('GET','HEAD','OPTIONS')
        last=None
        for endpoint in ENDPOINTS:
            if mutating: ha_store.fence()
            target=endpoint+u.path+('?' +u.query if u.query else '')
            try:
                response=await super().request(method,target,**kwargs)
            except (httpx.ConnectError,httpx.ConnectTimeout) as e:
                last=e
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
