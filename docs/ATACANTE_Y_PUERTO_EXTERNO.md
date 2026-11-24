# El atacante y el puerto externo del borde (estado medido el 24-nov-2026)

Qué es el puerto 1 del borde, qué hacen con él las reglas instaladas y qué hallazgo dejó. Todo salió del panel (pestaña Flujos, lectura del maestro) y de dos `ofproto/trace` en `switch-borde`; no cambió nada en la red.

## 1. Topología

```
  vm-atacante (host-jhon)
   ├─ ens3  10.20.11.203/24    red pública / gestión; no forma parte de la red SDN
   └─ ens4  203.0.113.103/24   IP fija, sin DHCP; MAC fa:16:3e:eb:ea:4c
        │  cable (sin cruzar todavía con la interfaz de VNRT)
        ▼
  switch-borde   ens4 = puerto 1 de OVS (MAC fa:16:3e:12:48:4c)
        ├─ puertos 2 y 3 → los cores (troncales: puertos de confianza)
        └─ puertos 4 y 5 → sensores node-sec-1/2 (red de monitoreo)
```

- Subred externa `203.0.113.0/24`, gateway `203.0.113.254`: lo contesta el propio borde con la MAC virtual `02:00:5e:00:00:fe` (app `campus.gatewayarp`).
- Ruta por defecto: el vigilante de capa 3 manda al puerto 1, con la MAC del atacante como siguiente salto, todo lo que no es red interna (`salida` en `R2/policy/planes/vnrt_topologia14.json`). Si cambia la tarjeta del atacante hay que actualizar esa MAC.
- Sin verificar: que los cables del borde y de los cores correspondan al dibujo de VNRT, y qué puerto del borde va a qué core.

## 2. Qué pasa con lo que entra por el puerto 1

El puerto 1 **no es de confianza**: no tiene la regla `40001 in_port=N, IPv4 → tabla 60` que sí tienen los puertos 2 y 3. Sus paquetes se deciden en la tabla 0, por prioridad:

| Prioridad | Tráfico | Resultado | Quién lo instala |
|---|---|---|---|
| 50000 | EAPOL (802.1X) | al controlador: puede autenticarse | mapper / meters |
| 41000 | IPv4 con origen `203.0.113.0/24` | copia al IDS (grupo 22001 → puertos 4 y 5) y **salta a la tabla 60** | `campus.espejo` (R5) |
| 41000 | IPv4 con origen `10.0.0.0/16` | igual que el anterior | `campus.espejo` (R5) |
| 40600 | DHCP | descartado | R2 |
| 40003, 40002 | ARP | tabla 60 (contesta el gateway virtual) | proactivo / R2 |
| 35000 | TCP a `10.0.0.10:8443` (portal) | permitido | R2 |
| 1000 | todo lo demás | VLAN 99 → tabla 99 → descartar | R2 |

Una sesión 802.1X o de portal añade una regla `40001 in_port=1, eth_src=<MAC>` (la instala el Policy Mapper al autenticarse, vía REST: `onos.rest`).

## 3. Lo comprobado con `ofproto/trace` (borde, TTL 64, TCP 8101 al server)

| Origen del paquete | Regla que lo atrapa | Resultado |
|---|---|---|
| `203.0.113.103` (el atacante real) | tabla 0, prio 41000 → tabla 60, prio 150 (`10.0.0.0/24`) | copia a los sensores y `output:2` hacia el core, TTL 63 |
| `10.0.1.99` (IP interna inventada) | **la misma** regla 41000, la de `10.0.0.0/16` | idéntico |

## 4. Hallazgo: el puerto externo no filtra el origen

- Los dos rangos que R5 espeja (`203.0.113.0/24` y `10.0.0.0/16`) llegan a la tabla 60 **sin pasar por la cuarentena ni por las tablas de rol**. Después los cores no tienen tablas de rol y los accesos confían en sus troncales: ninguna regla de R2 limita qué alcanza ese tráfico. Lo único que lo acota es lo que escuche cada máquina de destino (con `nc … 10.0.0.10 8101` el atacante recibe `Connection refused`: el paquete llega al server).
- Con un origen **interno inventado** pasa lo mismo. Las respuestas irían al dueño real de esa IP, así que sirve a ciegas, pero no es lo esperable de un puerto externo.
- Se corrigió un error mío: antes de la traza se dio por hecho que un origen distinto de `203.0.113.0/24` caía en cuarentena. No es así si el origen está en `10.0.0.0/16`.
- **Posible cambio (decisión con R5, no hecho):** que la regla 41000 copie al IDS pero que el tráfico siga a la cuarentena en vez de a la tabla 60, o añadir un descarte del `10.0.0.0/16` de origen en `in_port=1` por encima de 41000 (anti-suplantación). Cualquiera de las dos exige repetir la prueba del nmap de R5.

## 5. ¿Puede el atacante autenticarse en el portal?

El portal está en `https://10.0.0.10:8443` y el puerto 1 llega a él. Pero entrar exige dos cosas (ver `NOTA_PORTAL_MAC_POR_IP.md`):

1. **Que el portal identifique el equipo.** Toma la IP de origen del socket (no la que declare el cliente) y la busca en `GET /onos/v1/hosts`. Si ONOS no conoce esa IP, responde «No se pudo identificar su equipo» **antes de consultar LDAP**. El atacante tiene IP fija, sin DHCP, y ONOS no conocía su MAC al montar la salida; hay que comprobar si hoy la conoce.
2. **Credenciales LDAP válidas.** Sin ellas el mapper responde 401.

Si las dos se cumplieran, el mapper crearía una sesión de portal para la MAC del atacante y su flujo `40001 in_port=1` pondría el rol que corresponda (por ejemplo `guest`, que solo llega a `university_portal`). **Por eso el borde lleva las tablas de rol**: tiene un puerto por el que entran equipos.

Estado del portal (`NOTA_DESPLIEGUE_MAPPER_PORTAL_24NOV.md`): el parche del mapper que identifica al cliente por IP ya está desplegado en las tres réplicas y el `PORTAL_TOKEN` quedó puesto el 24-nov (la nota del 23-nov lo daba por vacío; ya no lo está), así que llamar a `/portal/login` o a `/portal/estado?ip=` por la red de gestión exige ese token.

Comprobación sin riesgo (desde `vm-atacante`, credenciales falsas como en la prueba de h2):

```
curl -sk -d 'usuario=x&clave=y' https://10.0.0.10:8443/login
```

«No se pudo identificar su equipo» significa que ONOS no lo conoce; «credenciales inválidas» (401), que sí lo identificó y falló en LDAP.

## 6. Por qué el portal no sale en la tabla de servicios del panel

El portal **no es un servicio de la matriz de roles**. La tabla «Servicios» y la matriz rol/servicio solo recogen lo que se permite a un rol una vez autenticado (por ejemplo `university_portal`, el 8003: una aplicación de la universidad). El portal cautivo es lo que se usa **antes** de tener sesión, y R2 lo trata aparte:

- bloque `portal` de la política (IP, MAC, puerto 8443 y switches donde actúa; se ve en la pestaña Políticas como la línea «Portal: …»);
- reglas de la tabla 0: `35000` (TCP al `10.0.0.10:8443`), `40002` (ARP del que no tiene sesión) y el desvío del HTTP;
- no consume flujos de las tablas de rol.

Las filas de la matriz con `internet` («tiene reglas pero ningún host: no se instala») es otro caso: un servicio con reglas pero sin IP en el catálogo.
