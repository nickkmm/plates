# Arquitectura

[← volver al índice](README.md)

## Componentes

Tres "puertas" de ingesta distintas convergen en el mismo pipeline de
`app/core` — `app/web` nunca escribe SQL directamente ni implementa reglas
de negocio, siempre pasa por `app/core`:

```mermaid
flowchart TD
    subgraph Puertas["Puertas de ingesta"]
        M["Formulario manual<br/>(/ingest)"]
        PDF["Upload de PDF<br/>(/ingest/pdf)"]
        MIG["Migración Excel<br/>(app/migrate.py, one-off)"]
    end
    M --> ER["ExtractedRow<br/>(app/core/models.py)"]
    PDF -->|"pdfplumber.extract_tables()"| ER
    MIG --> ER
    ER --> NORM["normalize_row<br/>(app/core/normalize.py)"]
    NORM --> PROC["process_rows / replace_order<br/>(app/core/ingestion.py)"]
    PROC --> DBL["app/core/db/ (paquete)"]
    DBL --> PG[(Postgres, vía pool)]
    PROC --> INV[recalc_inventory]
    INV --> DBL

    WEB["app/web/blueprints/*.py<br/>(rutas Flask)"] --> PROC
    WEB --> DBL
```

`app/core/ingestion.py` (renombrado de `process.py`) y `app/core/db/`
(dividido de un único `db.py`) son el resultado de la Fase 1d —
refactor de arquitectura por dominios sin agregar ORM/framework de DI/capa
repository, más un pool de conexiones real (`psycopg_pool`). Detalle
completo en [`CORE.md`](CORE.md).

Esto es lo que permite que la migración histórica de Excel
(`app/migrate.py`) reutilice exactamente la misma normalización y
detección de duplicados que usa el formulario web — un solo camino de
validación de reglas de negocio, no dos implementaciones paralelas.

Ver [`CORE.md`](CORE.md) para el detalle de cada módulo de este pipeline y
[`WEB.md`](WEB.md) para las rutas que lo disparan.

## Despliegue (Docker Compose)

```mermaid
flowchart LR
    subgraph VPS["VPS — /opt/plates"]
        subgraph internal["red: internal"]
            WEB["plates-web<br/>127.0.0.1:8000→8000"]
            DB[("plates-db-1<br/>127.0.0.1:5433→5432")]
        end
        subgraph edge["red: edge (informes_informes_net, external)"]
            WEB
            NGINX["nginx de la stack 'informes'<br/>(otro proyecto, mismo VPS)"]
        end
    end
    WEB -->|"DB_HOST=plates-db-1<br/>DB_PORT=5432"| DB
    NGINX -->|"proxy_pass → alias plates_web"| WEB
```

**Gotcha real de producción** (documentado también como comentario en
`docker-compose.yml`): `web` está en la misma red `edge` que la stack
vecina **"informes"**, y esa stack tiene su propio servicio llamado
también `db` (su MySQL). El DNS interno de Docker resolvía el hostname
ambiguo `db` hacia el servicio equivocado, causando errores 500 en
producción en cada ruta que tocaba la base de datos. La solución fue fijar
`container_name: plates-db-1` (único entre ambas stacks) y apuntar
`DB_HOST=plates-db-1` explícitamente en vez de depender del nombre de
servicio por defecto de Compose (`db`).

**Regla: nunca volver a `DB_HOST=db`.**

Puertos fijos en el host — solo puede correr una instancia de cada
servicio a la vez en una misma máquina:
- Postgres: host `5433` → contenedor `5432` (5432 en el host ya lo usa otro
  proyecto en el VPS).
- Web: host `8000` → contenedor `8000`.

Detalle de qué implica esto para levantar el proyecto en local (y para
correr agentes/tests en paralelo) en
[`LOCAL_DEVELOPMENT.md`](LOCAL_DEVELOPMENT.md).

## CI/CD

| Workflow | Trigger | Pasos |
|---|---|---|
| `.github/workflows/test.yml` | cada `push` y `pull_request` | Levanta Postgres 16 como servicio (puerto 5433) → `pip install -r requirements-dev.txt` → `ruff check app/` → `mypy app/` → `pytest --cov=app --cov-report=term-missing` |
| `.github/workflows/deploy.yml` | push a `main`, o `workflow_dispatch` manual | SSH al VPS (`appleboy/ssh-action`) → en `/opt/plates`: `git fetch --depth 1 origin main` → `git reset --hard origin/main` → decodifica el secreto `ENV_FILE_B64` a `.env` → `docker compose up -d --build` → `docker compose exec -T web python -m app.schema_migrate` |

`deploy.yml` **no vuelve a correr los tests** — el gate real es que
`test.yml` ya haya pasado en el push/PR que llega a `main` (no hay branch
protection que lo fuerce a nivel de este workflow). El VPS usa una **deploy
key** de solo lectura registrada en este repo (privado) para poder hacer
`git fetch` sin credenciales de escritura.

El paso final (`schema_migrate`) aplica cualquier migración pendiente de
`app/migrations/*.sql` (y sus backfills en Python) contra la base real,
registrándola en `schema_migrations` — correr el runner dos veces es un
no-op. Ver [`DATABASE.md`](DATABASE.md) para el runner y el historial de
migraciones ya aplicadas.

## Monitoreo y observabilidad (Fase 1e)

Alcance deliberadamente mínimo — nada de Prometheus/Grafana/ELK/APM, sería
sobreingeniería para un VPS con Docker Compose y un puñado de usuarios:

- **Sentry** (`sentry-sdk[flask]`): captura automáticamente cualquier
  excepción no manejada (con stack trace completo) y alerta por correo. Se
  inicializa en `app/web/app.py` justo después de crear `app`, solo si la
  variable de entorno `SENTRY_DSN` está seteada — en dev local y en los
  tests no se inicializa nada, así que no mandan datos a Sentry.
- **UptimeRobot** (externo, no toca el repo): ping a
  `https://placas.ingse.co/healthz` cada 5 minutos, alerta por correo si
  el sitio deja de responder.
- **Rotación de logs de Docker**: `docker-compose.yml` fija
  `logging: {driver: json-file, options: {max-size: "10m", max-file: "3"}}`
  en los servicios `db` y `web`, para que los logs de los contenedores no
  crezcan sin límite en el disco del VPS.
