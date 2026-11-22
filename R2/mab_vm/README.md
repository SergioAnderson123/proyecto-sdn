# R2 MAB de VM: estado compartido opcional

El modo actual sigue siendo local y conserva su comportamiento por defecto.
Para una réplica activa en varios controladores, `ha_state.py` permite compartir
el snapshot `children`/`revoking` con un clúster etcd mediante mTLS. Cada
operación serializada toma un lease en `/lock`, recarga `/state` y actualiza la
revisión con compare-and-swap. Si etcd no tiene quórum o se pierde el lease, el
servicio devuelve 503 y no admite una VM basándose en una copia local obsoleta.

## Protección del estado existente

- No habilitar HA en una segunda réplica con un volumen vacío antes de preparar
  la clave `/sdn/r2-mab/prod/state`.
- Si la clave no existe, el servicio falla cerrado. `R2_ETCD_INIT_EMPTY=1`
  solo permite inicializarla cuando el estado local está vacío. Usarlo una vez,
  verificar que la clave ya existe y retirar la variable antes de operar.
- Si el estado local contiene hijos o revocaciones y etcd aún no tiene snapshot,
  la inicialización se rechaza para evitar descartar sesiones. La migración de
  estado no se ejecuta automáticamente.
- El overlay `compose.ha.example.yml` es una plantilla; no se incluye en el
  compose base. Cada controlador necesita sus propios archivos mTLS con
  permisos restrictivos y un valor de `R2_ETCD_ENDPOINTS` accesible desde su
  red Docker. No guardar secretos en Git.

## Validación

```sh
cd R2/mab_vm
python3 -m unittest -v
```

`test_ha_state.py` simula dos instancias concurrentes, cuota compartida y
recuperación del snapshot tras reiniciar una instancia. Es una prueba unitaria;
no reemplaza una prueba contra un etcd de ensayo ni una caída real de R2 MAB.
Antes de desplegar, comprobar que el cliente mTLS puede llegar a los tres
miembros, sembrar el snapshot de forma controlada y hacer una prueba canaria
con puerto no autorizado. La sesión VM real debe probarse después en una
ventana coordinada.

## Migración sin perder el estado local

`migrate_state.py` permite importar el archivo actual `/state/sessions.json` a
la clave compartida solo si esa clave todavía no existe. Por defecto valida y
muestra el número de hijos/revocaciones sin escribir. `--apply` crea el estado
una sola vez bajo el mismo cerrojo distribuido que usa el servicio. Si etcd ya
tiene estado, termina sin sobrescribirlo. El archivo fuente nunca se modifica.

Para preparar tres réplicas, primero coordinar una ventana, detener solo el
contenedor R2 actual de ctrl-1 y copiar su archivo de estado a un respaldo
protegido. Validarlo con el nuevo contenedor en modo `run --rm` y luego ejecutar
la importación una vez, desde ctrl-1, con el prefijo y certificados mTLS de
producción. Confirmar el número de hijos en `GET /sessions` antes de habilitar
réplicas adicionales. Mantener intacto el volumen original y no activar
`R2_ETCD_INIT_EMPTY` para migrar un estado existente. Si la clave etcd ya existe,
no importar: investigar qué estado es el vigente.

Comandos de referencia en ctrl-1 (con VPN activa y el env-file privado ya
creado; no imprime sus valores):

```sh
cd ~/proyecto-sdn
export R2_ENV="$HOME/.config/r2-mab-ha.env"
dc() { docker compose --env-file "$R2_ENV" -p r2-mab-vnrt \
  -f R2/mab_vm/compose.yml \
  -f R1/deploy/controller/compose.mab-vnrt.yml \
  -f R2/mab_vm/compose.ha.example.yml "$@"; }
dc stop r2_mab_vm
install -m 600 /dev/null "$HOME/r2-sessions-pre-ha.json"
docker cp r2-mab-vm:/state/sessions.json "$HOME/r2-sessions-pre-ha.json"
dc run --rm --build \
  -v "$HOME/r2-sessions-pre-ha.json:/tmp/r2-sessions.json:ro" \
  r2_mab_vm python /r2/migrate_state.py /tmp/r2-sessions.json
# Revisar el conteo anterior; solo entonces repetir con --apply.
dc run --rm --build \
  -v "$HOME/r2-sessions-pre-ha.json:/tmp/r2-sessions.json:ro" \
  r2_mab_vm python /r2/migrate_state.py /tmp/r2-sessions.json --apply
dc up -d --build --no-deps r2_mab_vm
curl -fsS http://127.0.0.1:5001/sessions
```

Si falla la validación o el importador dice que etcd ya tiene estado, detenerse:
el volumen original y el archivo `r2-sessions-pre-ha.json` quedan intactos. No
iniciar ctrl-2/3 hasta verificar el snapshot compartido en ctrl-1.

En ctrl-2 y ctrl-3 usar el mismo prefijo, endpoints y certificados cliente
válidos para esos nodos, pero no ejecutar la importación. `R1_URL` debe apuntar
al mapper local (`policy_mapper:5000`) para que una caída del mapper de ctrl-1 no
desvíe todas las admisiones a ese nodo. `ONOS_URLS` contiene los tres REST de
ONOS; `VM_ACCESS_PORTS` debe describir únicamente puertos de acceso autorizados.
El overlay es de referencia y no se despliega automáticamente.

La recuperación HA de R2 aún debe probarse en VNRT: una admisión canaria válida,
lectura idéntica de sesiones desde las tres réplicas, caída de una instancia R2
y confirmación de que el padre pierde sus hijos al revocarse. No ejecutar esa
prueba con MAC, puertos o padres de usuarios reales sin una ventana coordinada.
