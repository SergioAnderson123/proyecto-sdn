# Compila la app de ONOS en Docker (Maven + JDK 11). Requiere Docker Desktop abierto. Resultado: target\gateway-arp-1.0.0.oar
# La caché de Maven vive en el volumen `onos-m2`: solo la primera compilación descarga las dependencias de ONOS.
$ErrorActionPreference = "Stop"
docker info *> $null
if ($LASTEXITCODE -ne 0) { Write-Error "El motor de Docker no responde: abre Docker Desktop y espera a que arranque."; exit 1 }
$aqui = (Get-Location).Path
docker run --rm -v "${aqui}:/app" -v onos-m2:/root/.m2 -w /app maven:3.8.6-eclipse-temurin-11 mvn -B -DskipTests package
if ($LASTEXITCODE -ne 0) { Write-Error "La compilación falló: revisa el mensaje de Maven."; exit 1 }
Get-ChildItem "$aqui\target\*.oar" | ForEach-Object { Write-Host "Generado:" $_.FullName ("{0:N0} bytes" -f $_.Length) }
