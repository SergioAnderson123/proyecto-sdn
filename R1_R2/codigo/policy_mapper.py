from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field
from typing import List, Optional
import uvicorn
import httpx

app = FastAPI(title="SDN Policy Mapper")

ONOS_API_URL = "http://localhost:8181/onos/aaa/app/users"
ONOS_AUTH = ("onos", "rocks")

ZONAS_SWITCH = {
    # Zona A: Laboratorio de Telecomunicaciones
    "of:0000000000000001": 100,
    
    # Zona B: Biblioteca 
    "of:0000000000000002": 200,
    
    # Zona C: Pabellón Administrativo
    "of:0000000000000003": 300,
}


class RadiusAttribute(BaseModel):
    type: str
    value: List[str]

class RadiusPayload(BaseModel):
    # Identidad Criptográfica (Para consultar el Rol en OpenLDAP)
    usuario_tls: RadiusAttribute = Field(alias="TLS-Client-Cert-Common-Name")
    
    # Identidad Externa/Enrutamiento (Para buscar el DPID en ONOS)
    usuario_externo: RadiusAttribute = Field(alias="User-Name")
    
    nas_ip: RadiusAttribute = Field(alias="NAS-IP-Address")
    # Podemos dejar puerto y MAC como opcionales por si en el futuro se arregla el AAA
    mac_fisica: Optional[RadiusAttribute] = Field(alias="Calling-Station-Id", default=None)
    switch_port: Optional[RadiusAttribute] = Field(alias="NAS-Port-Id", default=None)

@app.get("/sadis/{port_id:path}")
async def sadis_dinamico(port_id: str):
    # ONOS envía el nombre del puerto (ej. "s1-eth1" o "s25-eth4")
    # Extraemos el número del switch y lo convertimos a formato DPID
    try:
        if port_id.startswith("s"):
            # "s1-eth1" -> extrae "1" -> formatea a "of:0000000000000001"
            num_switch = port_id.split('-')[0].replace('s', '')
            dpid = f"of:{num_switch.zfill(16)}"
        else:
            # Si ONOS envía el formato nativo "of:0000.../1"
            dpid = port_id.split('/')[0]
            
        print(f"[SADIS] ONOS consultó puerto {port_id}. Respondiendo DPID: {dpid}")
        
        return {
            "id": port_id,
            "nasPortId": dpid,
        }
    except Exception as e:
        # Fallback seguro
        return {"id": port_id, "nasPortId": "of:0000000000000099"}

async def obtener_datos_onos(username_externo: str) -> dict:
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(ONOS_API_URL, auth=ONOS_AUTH, timeout=2.0)
            
            if response.status_code == 200:
                datos = response.json()
                print(f"\n[DEBUG ONOS] Respuesta de la API: {datos}")
                
                # Extraemos la lista
                if isinstance(datos, dict):
                    sesiones_aaa = datos.get("users", datos.get("entries", []))
                else:
                    sesiones_aaa = datos
                
                # Buscamos al usuario y extraemos su perfil completo
                if isinstance(sesiones_aaa, list):
                    for sesion in sesiones_aaa:
                        if isinstance(sesion, dict) and sesion.get("username") == username_externo:
                            perfil_onos = {
                                "dpid": sesion.get("subscriberId", "of:0000000000000099"),
                                "mac_onos": sesion.get("macAddress", ""),
                                "username_onos": sesion.get("username", "")
                            }
                            print(f"[ONOS API] Coincidencia encontrada: {username_externo} -> {perfil_onos['dpid']} | MAC: {perfil_onos['mac_onos']}")
                            return perfil_onos
                            
            print(f"[ONOS API] Usuario {username_externo} no encontrado en la tabla AAA.")
            
        except httpx.RequestError as e:
            print(f"[ONOS API] Error de conexión al controlador: {e}")
            
    # Retornamos None si falla, para que el webhook bloquee el acceso
    return None

@app.post("/webhook")
async def auth_webhook(payload: RadiusPayload, x_rol_ldap: str = Header(default="Sin Rol"), x_mac_ldap: str = Header(default="")):
    identidad_ldap = payload.usuario_tls.value[0]      # ej: "alumno-01"
    identidad_onos = payload.usuario_externo.value[0]  # ej: "user@example.org" o "anonimo"
    mac_esperada = x_mac_ldap.lower().strip()

    print(f"\n[>>>] INICIANDO ZERO TRUST: {identidad_ldap} ({x_rol_ldap})")
    # Vamos a ONOS a ver la realidad física de la red
    datos_onos = await obtener_datos_onos(identidad_onos)

    if not datos_onos:
        raise HTTPException(status_code=403, detail="Sesion no activa en controlador SDN")

    # VALIDACIÓN CAPA 2 (Device Binding)
    mac_fisica = datos_onos['mac_onos'].lower().strip()
    print(f"  [-] Validando MAC (LDAP: {mac_esperada} vs ONOS: {mac_fisica})")
    
    if mac_esperada != mac_fisica:
        print("  [X] RECHAZO: Alerta de MAC Spoofing.")
        return {"Tunnel-Type": "VLAN", "Tunnel-Medium-Type": "IEEE-802", "Tunnel-Private-Group-Id": "999"} # VLAN Cuarentena
    
    # ASIGNACIÓN FINAL (Rol + Zona)
    dpid = datos_onos['dpid']
    vlan_final = ZONAS_SWITCH.get(dpid, 99)

    if "docente" in x_rol_ldap.lower():
        vlan_final = 10 #
    print(f"  [V] APROBADO: Asignando VLAN {vlan_final}")

    print(f"      1. LDAP - Usuario Real : {identidad_ldap} (Rol: {x_rol_ldap})")
    print(f"      2. ONOS - Buscando a   : {identidad_onos}")
    print(f"      Resultado -> DPID: {datos_onos['dpid']} | MAC: {datos_onos['mac_onos']}")


    return {
        "Tunnel-Type": "VLAN",
        "Tunnel-Medium-Type": "IEEE-802",
        "Tunnel-Private-Group-Id": str(vlan_final),
        "Reply-Message": "Acceso Zero Trust Validado"
    }

if __name__ == '__main__':
    uvicorn.run("policy_mapper:app", host="0.0.0.0", port=5000, reload=True)