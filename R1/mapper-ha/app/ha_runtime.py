import asyncio
import time
import os
from ha_onos import AsyncClient
from fastapi.responses import JSONResponse
import ha_store

async def drain_outbox(mapper):
    store=mapper.session_store
    for entry in list(store.outbox.values()):
        # An identical match may have been safely re-authorized with the same flow id.
        current=store.get_by_mac(entry['mac'])
        if current and current.flow_id==entry['flow_id'] and current.dpid==entry['dpid']:
            store.acknowledge_delete(entry['dpid'],entry['flow_id'])
            continue
        await mapper.eliminar_flujo_onos(entry['dpid'],entry['flow_id'])

async def recover_intents(mapper):
    store=mapper.session_store
    for mac,intent in list(store.intents.items()):
        current=store.get_by_mac(mac)
        async with AsyncClient() as client:
            r=await client.get(f"{mapper.ONOS_BASE_URL}/flows/{intent['dpid']}",auth=mapper.ONOS_AUTH,timeout=3)
            r.raise_for_status()
            for f in r.json().get('flows',[]):
                criteria={x['type']:x for x in f.get('selector',{}).get('criteria',[])}
                if (str(f.get('priority'))=='40001' and str(f.get('tableId'))=='0'
                    and criteria.get('ETH_SRC',{}).get('mac','').lower()==mac
                    and str(criteria.get('IN_PORT',{}).get('port'))==intent['port']
                    and f.get('state')!='REMOVED'
                    and not (current and str(current.flow_id)==str(f['id']) and current.dpid==intent['dpid'])):
                    await mapper.eliminar_flujo_onos(intent['dpid'],f['id'])
                    # Recheck on the next tick before clearing the intent.
                    break
            else:
                store.clear_intent(mac)
    for mac,block in list(store.blocks.items()):
        remaining=int(block['until']-time.time())
        if remaining<=0:
            del store.blocks[mac]
            store.save()
        elif not block['applied']:
            await mapper.inyectar_blacklist_temporal(block['dpid'],block['port'],mac,remaining)

async def expire(mapper):
    for s in mapper.session_store.vencidas(time.time()):
        if s.base is not None:
            await mapper._restaurar_base(s,'vencida por tiempo')
        else:
            mapper.session_store.remove_session(s.mac)
            await mapper.eliminar_flujo_onos(s.dpid,s.flow_id)

async def ports(mapper):
    sessions=list(mapper.session_store._sessions_by_mac.values())
    if not sessions: return
    states=await mapper._puertos_sin_enlace(sorted({s.dpid for s in sessions}))
    close,mapper._puerto_caido_veces=mapper.macs_con_puerto_caido(sessions,states,mapper._puerto_caido_veces,mapper.PUERTO_CAIDO_VECES)
    for mac in close: await mapper._cerrar_por_ausencia(mac,'puerto sin enlace')

async def maintenance(mapper):
    while True:
        await asyncio.sleep(5)
        try:
            async with ha_store.transaction():
                state,_=mapper.session_store.client.get('maintenance')
                if state and time.time()-state.get('at',0)<4: continue
                await drain_outbox(mapper)
                await recover_intents(mapper)
                if time.time()-(state or {}).get('sync_at',0)>15:
                    await mapper._sincronizar_tablas()
                    sync_at=time.time()
                else:
                    sync_at=state['sync_at']
                await expire(mapper)
                # Port-down samples belong to one durable global counter, not each replica.
                mapper._puerto_caido_veces=(state or {}).get('down',{})
                await ports(mapper)
                _,rev=mapper.session_store.client.get('maintenance')
                mapper.session_store.client.cas('maintenance',{'at':time.time(),'down':mapper._puerto_caido_veces,'sync_at':sync_at},rev,ha_store.owner())
        except Exception as e:
            print('[HA] maintenance deferred:',type(e).__name__,flush=True)

def install(mapper):
    @mapper.app.middleware('http')
    async def shared_sessions(request,call_next):
        if request.url.path == '/sessions' and request.method == 'GET':
            try:
                state,_=await asyncio.to_thread(mapper.session_store.client.get,'state')
                if state is None: raise ha_store.Unavailable('migration missing')
                return JSONResponse({'total_sessions':len(state['sessions']),'sessions':state['sessions']})
            except Exception:
                return JSONResponse({'detail':'Session quorum unavailable'},status_code=503)
        if request.url.path in ('/health/ha','/docs','/openapi.json') or request.url.path.startswith('/sadis/'):
            return await call_next(request)
        try:
            async with ha_store.transaction():
                return await call_next(request)
        except ha_store.Unavailable:
            return JSONResponse({'detail':'Session quorum unavailable or operation in progress'},status_code=503)

    @mapper.app.get('/health/ha')
    async def health():
        try:
            state,rev=await asyncio.to_thread(mapper.session_store.client.get,'state')
            if state is None: raise ha_store.Unavailable('migration missing')
            return {'status':'ready','node':os.getenv('MAPPER_NODE'),'revision':rev,'sessions':len(state['sessions']),'pending_deletes':len(state.get('outbox',{}))}
        except Exception:
            return JSONResponse({'status':'unavailable'},status_code=503)

    @mapper.app.on_event('startup')
    async def startup():
        async with ha_store.transaction():
            print('[HA] recovered',len(mapper.session_store._sessions_by_mac),'sessions; no startup purge',flush=True)
        if os.getenv('HA_MAINTENANCE','1')=='1':
            mapper.app.state.ha_task=asyncio.create_task(maintenance(mapper))

    @mapper.app.on_event('shutdown')
    async def shutdown():
        task=getattr(mapper.app.state,'ha_task',None)
        if task: task.cancel()
