# Sigue los logs del Policy Mapper y se vuelve a enganchar si el contenedor se recrea (docker logs -f termina en ese caso).
while ($true) {
    docker logs -f --tail 25 policy_mapper
    Start-Sleep -Seconds 2
}
