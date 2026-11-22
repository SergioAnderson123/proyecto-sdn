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
