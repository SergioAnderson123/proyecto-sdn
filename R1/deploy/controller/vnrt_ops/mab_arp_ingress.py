"""Copia ARP de dos accesos al controlador y conserva el salto existente a T60."""
import json
from pathlib import Path
import sys
import time
import httpx
import policy_mapper as pm

APP='pe.edu.sdn.fallback-mab'
DEVICES=['of:0000e6925d87354b','of:0000766a0c84c741']
STATE=Path('/tmp/sdn-mab-ingress-state.json')
action=sys.argv[1] if len(sys.argv)>1 else 'apply'
with httpx.Client(auth=pm.ONOS_AUTH,timeout=10) as client:
    def flows(device):
        r=client.get(pm.ONOS_BASE_URL+'/flows/'+device);r.raise_for_status();return r.json().get('flows',[])
    def target(f):
        cs=f.get('selector',{}).get('criteria',[])
        return f.get('tableId')==0 and f.get('priority')==40004 and any(c.get('type')=='IN_PORT' and str(c.get('port'))=='1' for c in cs) and any(c.get('ethType','').lower() in ['0x806','0x0806'] for c in cs)
    if action=='apply':
        if STATE.exists():raise SystemExit('Ingress state already exists; inspect before applying')
        originals={}
        for device in DEVICES:
            fs=flows(device)
            if any(target(f) for f in fs):raise SystemExit('Existing overlapping priority40004 rule found; preserved')
            baseline=[f for f in fs if f.get('tableId')==0 and f.get('priority')==40003 and f.get('appId')=='r2.proactivo']
            if len(baseline)!=1:raise SystemExit('Expected original ARP pipeline not found; no rules installed')
            b=baseline[0]
            if b.get('treatment',{}).get('instructions')!=[{'type':'TABLE','tableId':'60'}]:raise SystemExit('Original pipeline changed; no rules installed')
            originals[device]=b
        bodies=[]
        for device in DEVICES:
            bodies.append({'deviceId':device,'tableId':0,'priority':40004,'timeout':0,'isPermanent':True,
              'selector':{'criteria':[{'type':'IN_PORT','port':1},{'type':'ETH_TYPE','ethType':'0x0806'}]},
              'treatment':{'instructions':[{'type':'OUTPUT','port':'CONTROLLER'},{'type':'TABLE','tableId':60}]}})
        r=client.post(pm.ONOS_BASE_URL+'/flows',params={'appId':APP},json={'flows':bodies});r.raise_for_status()
        state={'owner':APP,'originals':originals,'new':[]}
        for _ in range(16):
            found=[f for device in DEVICES for f in flows(device) if target(f) and f.get('appId')==APP and f.get('state')=='ADDED']
            if len(found)==2:
                state['new']=[{'device':f['deviceId'],'id':f['id']} for f in found];break
            time.sleep(.5)
        STATE.write_text(json.dumps(state))
        print('MAB_INGRESS_RULES_ADDED',len(state['new']))
        print('ORIGINAL_ARP_FORWARDING_PRESERVED',True)
        if len(state['new'])!=2:raise SystemExit('Ingress confirmation incomplete; inspect saved state before retry')
    elif action=='check':
        count=0
        for device in DEVICES:
            fs=flows(device)
            baseline=[f for f in fs if f.get('tableId')==0 and f.get('priority')==40003 and f.get('appId')=='r2.proactivo']
            if len(baseline)!=1 or baseline[0].get('treatment',{}).get('instructions')!=[{'type':'TABLE','tableId':'60'}]:
                raise SystemExit('Original ARP forwarding differs; inspect before changes')
            for f in fs:
                if target(f):
                    print('MAB_INGRESS',device,f.get('state'),'packets',f.get('packets'),'owner',f.get('appId'))
                    if f.get('state')=='ADDED' and f.get('appId')==APP:count+=1
        print('ORIGINAL_ARP_FORWARDING_PRESERVED',True)
        if count!=2:raise SystemExit('Expected two owned ADDED ingress rules; inspect saved state')
    elif action=='remove':
        state=json.loads(STATE.read_text())
        if state.get('owner')!=APP:raise SystemExit('State ownership mismatch; no removal')
        for row in state.get('new',[]):
            f=next((f for f in flows(row['device']) if f.get('id')==row['id']),None)
            if f and target(f) and f.get('appId')==APP:
                client.delete(pm.ONOS_BASE_URL+'/flows/'+row['device']+'/'+row['id']).raise_for_status()
        print('OWN_MAB_INGRESS_RULES_REMOVED')
    else:raise SystemExit('Unknown action')
