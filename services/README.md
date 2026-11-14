# Servicios web del campus – Docker Apache

## 1. Descripción general

Este proyecto implementa tres servidores web independientes utilizando contenedores Docker y la imagen oficial `httpd:2.4` de Apache.

Cada servidor representa un servicio diferente dentro de un entorno universitario:

- **Alumnos:** Portal informativo con servicios disponibles para estudiantes.
- **Notas:** Portal de consulta de información y calificaciones académicas.
- **Portal cautivo:** Página de bienvenida para los usuarios que se conectan a la red Wi-Fi del campus.

Los tres servidores se ejecutan simultáneamente en una misma máquina, pero cada uno funciona dentro de su propio contenedor.

## 2. Estructura del proyecto

```text
project/
├── docker-compose.yml
├── servidor-alumnos/
│   └── index.html
├── servidor-notas/
│   └── index.html
└── servidor-cautivo/
    └── index.html
```

### Descripción de los directorios

| Directorio | Contenido |
|---|---|
| `servidor-alumnos/` | Página principal del portal de alumnos. |
| `servidor-notas/` | Página principal del portal de calificaciones. |
| `servidor-cautivo/` | Página principal de bienvenida y acceso a la red. |

Cada directorio contiene su propio `index.html`, que Apache utiliza como página principal del servidor correspondiente.

## 3. Configuración de los servicios

Los servicios están definidos en `docker-compose.yml`:

| Servicio | Contenedor | Puerto del host | Puerto del contenedor |
|---|---|---:|---:|
| alumnos | s-alumnos | 8081 | 80 |
| notas | s-notas | 8082 | 80 |
| portal-cautivo | s-cautivo | 8083 | 80 |

Cada servicio utiliza:

- **Imagen:** `httpd:2.4`.
- **Servidor web:** Apache HTTP Server.
- **Volúmenes:** Se utiliza un volumen para asociar el directorio local correspondiente con `/usr/local/apache2/htdocs` dentro del contenedor.
- **Modo de montaje:** Solo lectura (`ro`).

Esto permite que cada contenedor tenga su propio contenido web y pueda funcionar independientemente de los otros servicios.

## 4. Acceso a los servidores

Una vez iniciados los contenedores, se puede acceder a cada página mediante las siguientes direcciones:

| Servicio | Dirección |
|---|---|
| Portal de alumnos | http://localhost:8081 |
| Portal de notas | http://localhost:8082 |
| Portal cautivo | http://localhost:8083 |

Si se accede desde otro equipo de la red, se debe reemplazar `localhost` por la dirección IP de la máquina donde se ejecuta Docker.

## 5. Ejecución

Para iniciar los tres servidores:

```bash
docker compose up -d
```

Para comprobar que los contenedores están funcionando:

```bash
docker ps
```

Para detener los servicios:

```bash
docker compose down
```

Para consultar los registros de ejecución:

```bash
docker compose logs -f
```

## 6. Consideraciones importantes

**Las tres páginas web son simulaciones (mockups) creadas exclusivamente para pruebas y demostraciones.**

Su finalidad es representar visualmente distintos servicios universitarios y comprobar el funcionamiento de múltiples servidores Apache ejecutándose simultáneamente mediante Docker.

Por lo tanto:

- No existe una conexión real con una base de datos.
- No se realiza autenticación de usuarios.
- No se consultan ni almacenan calificaciones reales.
- Los servicios de biblioteca, cafetería y espacios de estudio son únicamente representativos.
- El portal cautivo no implementa un mecanismo real de autenticación ni de control de acceso a la red.
- Los botones y enlaces son elementos visuales y no necesariamente conducen a servicios funcionales.

**En conclusión:** Este proyecto constituye un entorno de prueba para demostrar la publicación de múltiples servicios web independientes en una sola máquina mediante contenedores Docker y Apache.
