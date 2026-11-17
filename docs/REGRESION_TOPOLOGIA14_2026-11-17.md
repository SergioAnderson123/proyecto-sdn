# Integración de avances y regresión de topologia14-deploy

Solicitud del usuario: conservar lo de Daniel, comprobar compatibilidad e integrar sus avances de fallback en `topologia14-deploy`.

Base de Daniel: `32bd3c5da793bf67b20246cf02012f1dcc695c21`. Avance integrado: `c288441f110a8b7a23afefd85a1ec70d11c4ba6e`. Se preparó un merge en un worktree aislado, sin conflictos. Los siete commits nuevos de Daniel sobre presencia y meters se conservan completos.

## Resultados

| Comprobación | Resultado |
|---|---|
| R1, agente, despliegue, topología14 y política R2 | 458 pasan, 1 omitida |
| MAB VM | 22 pasan |
| R5 | 111 pasan; su código no se modificó |
| Arranque compatible LDAP | 3 pasan |
| App Java DHCP/presencia | 35 pasan |
| App Java meters | 7 pasan |
| Total de esta regresión | 636 pasan, 1 omitida |
| LDAP nuevo aislado | Bootstrap original de osixia y consulta autenticada correctos; contenedor temporal retirado |
| EAP real de alumno | Una reautenticación, 4 segundos, Alumno/VLAN 20 |
| Clúster y switches | 3 READY, 5 dispositivos disponibles |
| Apps anteriores | AAA, SADIS, gateway ARP, DHCP/presencia y meters siguen ACTIVE |
| Meters en ambos accesos | EAPOL 20 pps y DHCP 10 pps, ADDED; banda DROP EAPOL con cero descartes al consultar |
| Reglas de Daniel | Reglas proactivas, gateway ARP, DHCP y meters presentes y ADDED, sin pendientes en la consulta |
| Docente → servidor TCP 8101 | Connection refused: el transporte llega y vuelve, como exige PLAN_CONMUTACION_CAPA3.md |
| Alumno → servidor TCP 8101 | Timeout: bloqueo por rol, como exige el mismo protocolo |
| ICMP | No es prueba positiva de conectividad en esta política: R2 lo descarta por diseño |

Las pruebas Python se ejecutaron en contenedores sin red y con repositorio de solo lectura. Las Java se ejecutaron como procesos separados, sin instalar ni activar apps nuevas. No se ejecutaron las inyecciones masivas de 91 ni la desconexión de switches de 92.

## Compatibilidad LDAP corregida antes de integrar

La variante inicial de persistencia arrancaba directamente slapd: conservaba el despliegue migrado, pero no inicializaba una instalación vacía. Se añadió `R1/shared/ldap/start-compatible.sh`:

- Con `cn=config.ldif` existente, ejecuta el mismo slapd directo que conserva TLS y multimaestro.
- Con datos/configuración vacíos, conserva `/container/tool/run --copy-service`, el bootstrap original de la imagen.
- Con datos parciales, aborta en vez de inicializar encima.

El script y las pruebas se prepararon fuera del despliegue activo. No se reinició el LDAP de producción para probar el arranque. Los directorios privados persistentes siguen ignorados por Git y no se publican.

## Límites que permanecen

No se afirma que todos los escenarios del proyecto estén validados en vivo: faltan SSH de Jhon, una nueva emisión física MAB desde h1 tras la corrección del punt, la reconexión de meters que Daniel documentó como pendiente y cualquier servicio de aplicación que no esté escuchando en el servidor. Se conserva la evidencia previa de MAB positivo mediante una única trama en el pipeline OVS.

No se cambian R5, los certificados EAP-TLS, las identidades de usuarios ni las reglas originales de Daniel. La integración Git no requiere reiniciar la topología.
