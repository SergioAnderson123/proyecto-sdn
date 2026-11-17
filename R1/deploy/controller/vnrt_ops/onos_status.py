"""Resumen ONOS/R1 usando el cliente ya configurado, sin mostrar credenciales."""
import json
import urllib.request
import httpx
import policy_mapper as pm

with httpx.Client(auth=pm.ONOS_AUTH,timeout=8) as client:
    def get(path):
        r=client.get(pm.ONOS_BASE_URL+path);r.raise_for_status();return r.json()
    print('CLUSTER',[(n['ip'],n['status']) for n in get('/cluster').get('nodes',[])])
    apps=get('/applications').get('applications',[])
    print('MAB_APPLICATIONS',[(a['name'],a['state']) for a in apps if 'mab' in a['name'].lower() or 'fallback' in a['name'].lower()])
    devices=get('/devices').get('devices',[])
    print('DEVICES',[(d['id'],d['available']) for d in devices])
    for d in devices:
        fs=get('/flows/'+d['id']).get('flows',[])
        flood=sum(any(i.get('type')=='OUTPUT' and str(i.get('port')).upper() in ['NORMAL','FLOOD','ALL'] for i in f.get('treatment',{}).get('instructions',[])) for f in fs)
        eapol=sum(f.get('priority')==50000 and any(c.get('ethType','').lower()=='0x888e' for c in f.get('selector',{}).get('criteria',[])) for f in fs)
        tests=sum(any(str(c.get('mac','')).lower().startswith('02:cd:00:00:00:') for c in f.get('selector',{}).get('criteria',[])) for f in fs)
        print('FLOW_SUMMARY',d['id'],'flood_actions',flood,'eapol_port_rules',eapol,'test_flows',tests)
with urllib.request.urlopen('http://127.0.0.1:5000/sessions',timeout=5) as r:data=json.load(r)
print('R1_SESSIONS',[{k:v for k,v in s.items() if k in ['dpid','port','rol','vlan','state']} for s in data.get('sessions',{}).values()])
with urllib.request.urlopen('http://r2-mab-vm:5001/sessions',timeout=5) as r:data=json.load(r)
print('MAB_CHILDREN',len(data.get('sessions',[])))
