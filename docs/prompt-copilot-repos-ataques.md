# Prompt para GitHub Copilot: repositorios para simular ataques en el laboratorio de R5

Pégalo en Copilot Chat (modo con búsqueda en GitHub/web). Está pensado para **probar un IDS propio en un laboratorio aislado**, no para atacar redes reales.

## Prompt principal

```text
Contexto: soy estudiante de Redes Definidas por Software (PUCP). Mi proyecto es una defensa perimetral: Suricata (IDS fuera de banda, reglas ET Open) recibe una copia del tráfico de un switch Open vSwitch controlado por ONOS (OpenFlow 1.3); un script en Python lee eve.json y pide a ONOS un DROP por IP. Todo corre en un laboratorio aislado con Mininet en Docker (red virtual 10.0.0.0/24 interna y 10.0.1.0/24 externa). Necesito PROBAR la defensa con tráfico de ataque generado dentro de ese laboratorio. No voy a atacar ninguna red real.

Busca en GitHub repositorios activos y con licencia clara que me sirvan para generar o reproducir, dentro de un laboratorio aislado, estos tipos de tráfico:
1. Escaneos de red y de puertos (SYN, connect, barridos rápidos) y variantes de evasión simples (fragmentación, escaneo lento).
2. Inundaciones y DoS a nivel de laboratorio (SYN flood, UDP/ICMP flood, HTTP flood) con tasa de paquetes controlable.
3. Ataques a aplicaciones web de prueba (inyección SQL, fuerza bruta HTTP/SSH, escáneres web) contra aplicaciones deliberadamente vulnerables (DVWA, Juice Shop, WebGoat).
4. Simulación de hosts comprometidos: balizas C2 de prueba, exfiltración por DNS/HTTP, tráfico de botnet simulado (sin malware real).
5. Archivos PCAP etiquetados con ataques para reproducir con tcpreplay o Scapy (CIC-IDS2017/2018, UNSW-NB15, CTU-13, Stratosphere, malware-traffic-analysis) y generadores de tráfico legítimo para medir falsos positivos (tráfico web/DNS/NAT).
6. Frameworks de emulación de adversario o de pruebas de detección: Atomic Red Team, MITRE Caldera, Scapy, Metasploit en modo laboratorio, o proyectos que prueben reglas de Suricata (por ejemplo, validación de reglas con pcaps).
7. Repositorios que ya integren Mininet o SDN con IDS/IPS (Mininet + Suricata/Snort, ONOS/Ryu/Floodlight con detección de DDoS o port scan).

Para cada repositorio entrégame una tabla con: nombre y enlace; qué tipo de tráfico genera; licencia; último commit y estrellas; lenguaje y cómo se ejecuta (comando de ejemplo); si funciona dentro de Mininet o un contenedor sin hardware especial; qué reglas o firmas de Suricata/ET suele disparar (si lo documenta); y riesgos (por ejemplo, si trae malware real o necesita internet).

Criterios de selección:
- Prefiere herramientas que permitan limitar la tasa (paquetes/s) y repetir el experimento con n >= 30 corridas.
- Descarta repositorios abandonados (sin cambios en 2 años), sin licencia, o que contengan malware real, ransomware, robo de credenciales o exploits contra servicios públicos.
- Prioriza datasets y PCAP con etiquetas de verdad (qué es ataque y qué es legítimo), porque necesito calcular tasa de detección y de falsos positivos.

Al final dame: (a) las 5 mejores opciones para mi caso, ordenadas, con un plan de uso en 3 pasos cada una dentro de Mininet; (b) una lista de comandos de búsqueda de GitHub que pueda reutilizar; (c) una advertencia breve sobre qué NO debo ejecutar fuera de mi laboratorio.
```

## Versión corta

```text
Busca en GitHub herramientas y PCAPs con licencia clara para generar, dentro de un laboratorio aislado con Mininet + Suricata + ONOS, estos tráficos: port scan (SYN/ráfaga/lento), SYN/UDP/HTTP flood con tasa controlable, ataques web contra DVWA/Juice Shop, balizas C2 simuladas sin malware real, y PCAPs etiquetados (CIC-IDS2017, UNSW-NB15, CTU-13) para tcpreplay. Dame una tabla (enlace, qué genera, licencia, último commit, cómo ejecutarlo, firmas ET que dispara) y las 5 mejores para medir tasa de detección, falsos positivos y pérdida de captura. Descarta proyectos abandonados o con malware real.
```

## Búsquedas manuales en GitHub (por si Copilot no alcanza)

| Qué | Búsqueda en github.com/search |
|---|---|
| Mininet + Suricata/Snort | `mininet suricata language:Python pushed:>2024-01-01` |
| SDN + detección de DDoS/port scan | `onos OR ryu ddos detection mininet pushed:>2023-06-01` |
| Pruebas de reglas Suricata con PCAP | `suricata rules test pcap pushed:>2024-01-01` |
| Generadores de tráfico controlable | `traffic generator scapy syn flood lab` / `tcpreplay pcap attack dataset` |
| Datasets etiquetados | `CICIDS2017 OR UNSW-NB15 OR CTU-13 pcap` |
| Aplicaciones vulnerables de prueba | `DVWA OR juice-shop OR webgoat docker` |
| Emulación de adversario | `atomic-red-team` / `mitre caldera` |

## Antes de ejecutar cualquier cosa que encuentres

1. Léelo: no ejecutes scripts de un repositorio que no hayas revisado, sobre todo si piden `sudo`, descargan binarios o contactan servidores externos.
2. Ejecútalo solo dentro del contenedor del laboratorio (`r5-lab`) y contra los hosts de prueba (`10.0.0.0/24`, `10.0.1.0/24`).
3. Limita la tasa y la duración; no apuntes nada a la red de la universidad, a tu red doméstica ni a terceros.
4. Guarda el PCAP, `eve-alerts.json` y el log del integrador de cada corrida: son la evidencia para la ficha de autoevaluación.
