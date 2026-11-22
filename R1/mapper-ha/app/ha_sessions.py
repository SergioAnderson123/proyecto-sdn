"""SessionManager implementation with durable, replicated snapshots and deletion outbox."""
from dataclasses import asdict
import copy
import time
import ha_store


def decode(cls, data):
    d=dict(data)
    if d.get('base') is not None: d['base']=decode(cls,d['base'])
    return cls(**d)


def persistent_manager(base_cls, session_cls):
    class PersistentSessions(base_cls):
        def __init__(self):
            super().__init__()
            self.client=ha_store.Etcd()
            self.revision=0
            self.outbox={}
            self.intents={}
            self.blocks={}
        def refresh(self):
            state, rev=self.client.get('state')
            if state is None:
                raise ha_store.Unavailable('State not initialized: explicit migration required')
            if state.get('schema')!=1: raise ha_store.Unavailable('Unsupported session schema')
            sessions=[decode(session_cls,s) for s in state['sessions'].values()]
            self._sessions_by_mac={}
            self._sessions_by_user={}
            self._sessions_by_port={}
            for s in sessions: super().register_session(s)
            self.outbox=copy.deepcopy(state.get('outbox',{}))
            self.intents=copy.deepcopy(state.get('intents',{}))
            self.blocks=copy.deepcopy(state.get('blocks',{}))
            self.revision=rev
        def snapshot(self):
            return {'schema':1,'sessions':{k:asdict(v) for k,v in self._sessions_by_mac.items()},'outbox':self.outbox,'intents':self.intents,'blocks':self.blocks}
        def save(self):
            self.revision=self.client.cas('state',self.snapshot(),self.revision,ha_store.owner())
        def register_session(self,session):
            ha_store.fence()
            # Remove stale indices if this MAC is re-registered at another location/user.
            old=self._sessions_by_mac.get(session.mac.lower())
            if old: super().remove_session(old.mac)
            super().register_session(session)
            self.intents.pop(session.mac.lower(),None)
            self.save()
        def remove_session(self,mac):
            ha_store.fence()
            s=super().remove_session(mac)
            if s:
                if s.flow_id: self.outbox[f'{s.dpid}/{s.flow_id}']=asdict(s)
                self.save()
            return s
        def plan_admission(self,dpid,port,mac,vlan):
            self.intents[mac.lower()]={'dpid':dpid,'port':str(port),'mac':mac.lower(),'vlan':vlan,'at':time.time()}
            self.save()
        def clear_intent(self,mac):
            self.intents.pop(mac,None)
            self.save()
        def plan_block(self,mac,seconds):
            s=self.get_by_mac(mac)
            if s:
                self.blocks[mac]={'dpid':s.dpid,'port':str(s.port),'mac':mac,'until':time.time()+seconds,'applied':False}
                self.save()
        def acknowledge_block(self,mac):
            if mac in self.blocks:
                self.blocks[mac]['applied']=True
                self.save()
        def acknowledge_delete(self,dpid,flow_id):
            key=f'{dpid}/{flow_id}'
            if key in self.outbox:
                del self.outbox[key]
                self.save()
    return PersistentSessions
