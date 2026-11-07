"""Prueba de aceptación de R2: cada rol autenticado debe alcanzar solo los servicios que `policy.yaml` le permite.

Lee la matriz de policy.yaml (la misma que instala el Policy Mapper), hace `curl` desde el host de cada usuario
dentro del contenedor `mininet` y compara lo observado con lo esperado. Requisitos: stack levantado, topología en
marcha, servidores de prueba (R2/mininet/servicios_r2.sh) y los usuarios autenticados con wpa_supplicant.

Uso (desde R2):  python tests/aceptacion_r2.py
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "R1", "shared", "policy_mapper"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "policy"))
import policy_tables as pt  # noqa: E402

# host de Mininet -> rol de LDAP del usuario autenticado en él
USUARIOS = {"h1": "docente", "h2": "alumno", "h3": "soporte"}


def curl(host: str, ip: str, puerto: int) -> str:
    cmd = ["docker", "exec", "mininet", "bash", "/root/shared-host/mnx.sh", host,
           "curl", "-s", "-m", "3", "-o", "/dev/null", "-w", "%{http_code}", f"http://{ip}:{puerto}/"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
    return out if out and out != "000" else "bloqueado"


def main() -> int:
    pol = pt.cargar_politica()
    fallos, total = 0, 0
    print(f"{'host':<5}{'rol':<9}{'servicio':<20}{'esperado':<10}{'observado':<11}resultado")
    for host, rol in USUARIOS.items():
        permitidos = pt.servicios_permitidos(pol, pol["roles"][rol]["matriz"])
        for nombre, srv in sorted(pol["services"].items()):
            esperado = "permitido" if permitidos.get(nombre) else "bloqueado"
            codigo = curl(host, srv["ip"], int(srv["tcp"]))
            observado = "permitido" if codigo == "200" else "bloqueado"
            ok = esperado == observado
            total += 1
            fallos += 0 if ok else 1
            print(f"{host:<5}{rol:<9}{nombre:<20}{esperado:<10}{observado:<11}{'OK' if ok else 'FALLA'}")
    print(f"\n{total - fallos} de {total} casos correctos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
