# Protocolo: primera prueba de autenticación sin provocar una tormenta

Objetivo: autenticar usuarios de prueba (802.1X, DHCP, portal) en la topología de 14 VM **sin generar una tormenta de broadcast**. Todo se lanza desde
`switch-borde`, en `R1/deploy/topologia14`, con `PLAYBOOK=<archivo> ./lanzar.sh` (la contraseña de `ubuntu` se pide). Diseño: `docs/DISENO_FASE_E.md`.

**Regla general:** un solo usuario, un solo switch, un paso a la vez. Antes y después de cada paso se corre el diagnóstico 95. Si algo no cuadra, se
para y se vuelve atrás; no se sigue "a ver qué pasa". Nada de esto cambia la red sin que lo ejecutes tú.

## 0. Qué puede y qué no puede pasar

- **ARP** ya no inunda: la tabla 0 lo manda a la tabla 60 y se reparte por el árbol de difusión (probado en vivo, sin STP).
- **Mientras el switch siga con `reenvio: normal`**, el tráfico IP de un usuario autenticado sale de su tabla de rol con `OUTPUT NORMAL`, que **sí inunda**
  si el destino no es conocido. La tabla de rol solo deja pasar ARP y TCP a los servicios, así que el riesgo es bajo, pero no es cero. Por eso el
  primer login se hace con el switch del usuario ya en reenvío proactivo.
- **En reenvío proactivo se falla cerrado:** si a un host le falta su regla, su tráfico se **descarta**; no se inunda. Lo peor que puede pasar es que no haya
  conectividad (eso se diagnostica sin riesgo, ver paso 6), no una tormenta.
- El **ICMP (ping) está descartado** para los usuarios con sesión: no uses ping para probar. Usa TCP a un puerto permitido a su rol (p. ej. el 8001 de la
  biblioteca para un alumno) o el portal (8443).

## 1. Antes de empezar (lista de comprobación)

- [ ] Los 14 enlaces vivos en ONOS y los 5 switches disponibles: `PLAYBOOK=90_estado.yml ./lanzar.sh`.
- [ ] Diagnóstico base limpio: `PLAYBOOK=95_diagnostico_lazos.yml ./lanzar.sh` (sin tormenta, CPU de `ovs-vswitchd` baja). Guarda esta salida.
- [ ] El vigilante activo (principal en node-ctrl-3 y respaldo en node-ctrl-2): `journalctl -u vigilante -n 20` en node-ctrl-3.
- [ ] El freno a mano, en otra terminal ya abierta en `switch-borde` (solo se escribe el comando, no se pulsa Enter):
      `PLAYBOOK=99_freno_emergencia.yml ./lanzar.sh -e confirmar=true`
- [ ] Una segunda terminal con la CPU de los switches a la vista: `top -b -n1 | grep ovs-vswitchd` (en el switch que se prueba).
- [ ] Sabes qué host de prueba cuelga de qué switch (`TOPOLOGIA.md`; ojo: sw-acc-1 es la VM 192.168.201.12 y sw-acc-2 la .11).
- [ ] La rama subida y los repos de los nodos al día (los playbooks 70 y 65 actualizan el código solos).

**Criterio de parada (cualquiera de estos, en cualquier paso):**
1. el diagnóstico 95 falla (rojo) o marca un puerto por encima de 2000 pps en dos o más puertos a la vez;
2. `ovs-vswitchd` sostenido por encima del 80 % de CPU en algún switch;
3. el contador de paquetes de un puerto troncal crece sin parar tras quedarse el usuario quieto.
→ **freno de inmediato** (ver sección 7) y no se continúa hasta entender la causa.

## 2. Paso A: línea base de hosts (solo lectura)

```bash
PLAYBOOK=71_consulta_hosts.yml ./lanzar.sh -e guardar=/tmp/hosts_0_base.json
```
Anota qué hosts conoce ONOS sin ningún usuario conectado. No cambia nada.

## 3. Paso B: el server (IP única y hosts fijos)

No genera tráfico nuevo hacia la red de usuarios. Se hace antes de tocar los switches.

```bash
PLAYBOOK=70_fase_e.yml ./lanzar.sh                              # solo lee las MAC e imprime el policy.local.yaml: revisa que sea lo esperado
PLAYBOOK=70_fase_e.yml ./lanzar.sh -e preparar_ip=true          # 10.0.0.10/24 en ens4 del server (temporal)
PLAYBOOK=70_fase_e.yml ./lanzar.sh -e hosts_fijos=true          # escribe /etc/vigilante/hosts_fijos.json en node-ctrl-3 y node-ctrl-2
PLAYBOOK=65_vigilante.yml ./lanzar.sh -e usar_hosts_fijos=true -e aplicar=true
```
Qué mirar: en `journalctl -u vigilante -f` debe salir **ALTA RÁPIDA** con las reglas unicast del server (una por switch, nada que borrar). Ejecuta el 95:
no debe cambiar nada. Si el vigilante dice "NO ARRANCO", el archivo de hosts fijos está mal: no se sigue.

## 4. Paso C: reenvío proactivo en cores y borde

Estos switches solo llevan reglas de confianza (no hay usuarios), así que el cambio es pequeño y no afecta a nadie todavía.

```bash
PLAYBOOK=70_fase_e.yml ./lanzar.sh -e escribir=true -e reenvio=core-1,core-2,borde        # escribe policy.local.yaml, aún sin reiniciar
PLAYBOOK=70_fase_e.yml ./lanzar.sh -e escribir=true -e reenvio=core-1,core-2,borde -e reiniciar=true
PLAYBOOK=95_diagnostico_lazos.yml ./lanzar.sh
```
Qué mirar: en el 95, los switches `core-1`, `core-2` y `borde` ya **no** deben tener flujos con NORMAL de R2 (los de R5 o de ONOS pueden seguir). Comprueba además
que ONOS sigue viendo los 14 enlaces (LLDP sube por la regla propia de prioridad 40100): `PLAYBOOK=90_estado.yml ./lanzar.sh`.
Pendiente de verificar en vivo: que ONOS **reemplace** el flujo de misma coincidencia y prioridad al cambiar su acción de NORMAL a la tabla 60; si el 95 aún
muestra el NORMAL antiguo, no se continúa (se borra a mano ese flujo con el diagnóstico 95 delante).

## 5. Paso D: sw-acc-1 en proactivo, sin usuarios todavía

```bash
PLAYBOOK=70_fase_e.yml ./lanzar.sh -e escribir=true -e reenvio=core-1,core-2,borde,sw-acc-1 -e reiniciar=true
PLAYBOOK=95_diagnostico_lazos.yml ./lanzar.sh
```
(El nombre del switch en `reenvio` es el de `plan_arboles.NOMBRES`: `acc-1`, no `sw-acc-1`. Si el generador dice "switch desconocido", corrige el nombre.)
Ahora `acc-1` debe estar sin NORMAL de R2. Espera 1–2 minutos con el 95 limpio antes del primer login.

## 6. Paso E: el primer usuario (un solo equipo, en sw-acc-1)

1. Terminal del vigilante abierta (`journalctl -u vigilante -f` en node-ctrl-3) y la del freno lista.
2. Inicia sesión **un** usuario por 802.1X en el host de sw-acc-1.
3. En cuanto salga la sesión, **antes** de generar más tráfico:
   ```bash
   PLAYBOOK=95_diagnostico_lazos.yml ./lanzar.sh
   PLAYBOOK=71_consulta_hosts.yml ./lanzar.sh -e guardar=/tmp/hosts_1_login.json
   ```
4. Mira dos cosas: (a) el 95 limpio; (b) si la MAC del usuario aparece en `HOSTS que ONOS conoce`.
   - **Aparece:** el vigilante la verá y dará **ALTA RÁPIDA**; en unos segundos el usuario resuelve ARP y llega a sus servicios.
   - **No aparece** ("con sesión SIN host" en el cruce): no es una tormenta; el usuario simplemente no recibirá respuestas (el ARP del server hacia su MAC
     se descarta por falta de regla: falla cerrado). Esa es la señal de que hace falta `-e copiar_arp=true` en el vigilante
     (`PLAYBOOK=65_vigilante.yml ./lanzar.sh -e usar_hosts_fijos=true -e copiar_arp=true -e aplicar=true`), o aprender la MAC de la sesión.
5. Prueba la conectividad con TCP permitido a su rol (no ping): `curl -m 5 http://10.0.0.10:8001` (alumno: biblioteca) y comprueba que un servicio **no** permitido
   (`:8101`, notas) no responde.
6. Repite el 95 tras la prueba. Deja el usuario conectado unos minutos y vuelve a medir: la CPU y los contadores deben quedar planos.

## 7. Marcha atrás (de menos a más drástica)

1. **Volver a NORMAL en un switch o en todos** (reversible, no borra nada): `PLAYBOOK=70_fase_e.yml ./lanzar.sh -e escribir=true -e reenvio=normal -e reiniciar=true`.
2. **Pausar el vigilante** (no toca sus flujos, solo deja de cambiar cosas): `sudo touch /etc/vigilante/desactivar` en node-ctrl-3 y node-ctrl-2 (reanudar: `sudo rm`).
3. **Freno de emergencia** (tormenta en curso): `PLAYBOOK=99_freno_emergencia.yml ./lanzar.sh -e confirmar=true`. Para el Policy Mapper y borra los flujos de R1 y R2
   (los que llevan NORMAL). **No** borra los de la tabla 60 ni la regla del ARP (appId `r2.proactivo`), así que el ARP sigue repartiéndose por el árbol. Para volver:
   `docker start policy_mapper` en node-ctrl-1.
4. Si la tormenta sigue tras el freno, hay un lazo abierto por otro camino: bajar a mano el puerto del lazo en un extremo (`sudo ip link set <interfaz> down`).
5. Quitar todo lo proactivo (último recurso, el ARP volvería al controlador): `PLAYBOOK=60_proactivo.yml ./lanzar.sh -e fase=quitar -e aplicar=true` (borra todo lo de `r2.proactivo` en los 5 switches; el vigilante lo volvería a montar si sigue activo: pausarlo antes, punto 2).

## 8. Después, el resto

Con el primer usuario bien: repetir con el invitado (DHCP + portal, un equipo sin 802.1X) y, por último, con host-jhon (IP fija, sin DHCP: se espera que **no** obtenga IP).
Luego `sw-acc-2` (añadir `acc-2` a la lista de `reenvio`), con el mismo ciclo: 95 antes, un usuario, 95 después. Solo cuando ambos accesos estén bien, `reenvio=proactivo`
para todos. Después de cada paso con éxito se anota el resultado (hora, salida del 95, hosts vistos) para la memoria del proyecto.
