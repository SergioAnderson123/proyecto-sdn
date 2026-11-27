# University Services - SDN Project

## Service Catalog

<table>
  <thead>
    <tr>
      <th>Service</th>
      <th>IP</th>
      <th>TCP Port</th>
      <th>Note</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>virtual_library</td>
      <td>10.0.0.10</td>
      <td>8001</td>
      <td>—</td>
    </tr>
    <tr>
      <td>learning_platform</td>
      <td>10.0.0.10</td>
      <td>8002</td>
      <td>—</td>
    </tr>
    <tr>
      <td>university_portal</td>
      <td>10.0.0.10</td>
      <td>8003</td>
      <td>—</td>
    </tr>
    <tr>
      <td>student_records</td>
      <td>10.0.0.10</td>
      <td>8102</td>
      <td>—</td>
    </tr>
    <tr>
      <td>research_database</td>
      <td>10.0.0.10</td>
      <td>8103</td>
      <td>—</td>
    </tr>
    <tr>
      <td>print_server</td>
      <td>10.0.0.10</td>
      <td>8104</td>
      <td>—</td>
    </tr>
    <tr>
      <td>network_management</td>
      <td>10.0.0.10</td>
      <td>8105</td>
      <td>—</td>
    </tr>
    <tr>
      <td>grade_system</td>
      <td>10.0.0.10</td>
      <td>8101</td>
      <td>—</td>
    </tr>
    <tr>
      <td>internet</td>
      <td>—</td>
      <td>—</td>
      <td>has rules but no host: not deployed</td>
    </tr>
  </tbody>
</table>

---

## Quick Start

### Prerequisites
- [Docker](https://docs.docker.com/get-docker/) and Docker Compose installed

### Run All Services

```bash
# From this directory (services/)
docker compose up -d
```

This starts 8 Apache containers serving static content from each service folder.

### Verify Services

| Service | URL |
|---------|-----|
| Virtual Library | http://localhost:8001 |
| Learning Platform | http://localhost:8002 |
| University Portal | http://localhost:8003 |
| Student Records | http://localhost:8102 |
| Research Database | http://localhost:8103 |
| Print Server | http://localhost:8104 |
| Network Management | http://localhost:8105 |
| Grade System | http://localhost:8101 |

### Stop Services

```bash
docker compose down
```

### View Logs

```bash
docker compose logs -f [service_name]
```

### Project Structure

Each service directory (e.g., `grade_system/`) contains static HTML/CSS/JS files served by Apache. Edit files directly—changes reflect immediately (volumes are mounted read-only).

---

> **Note**: The `internet` service defines firewall rules only and has no container/host to deploy.