# Desarrollo local

[← volver al índice](README.md)

Para el quickstart básico (`docker compose up -d`, generar hashes de auth)
ver el [`README.md`](../README.md) raíz — acá solo los gotchas que ese
README no cubre.

## Gotchas de infra

- **Nombres y puertos de contenedor fijos**: `plates-db-1` (host `5433`) y
  `plates-web` (host `8000`). Solo puede correr **una** instancia de cada
  uno a la vez en una misma máquina — si necesitás levantar el proyecto
  desde dos worktrees/ramas distintas en paralelo (ej. dos agentes
  trabajando a la vez), tienen que coordinarse para no pisarse el mismo
  contenedor/puerto.
- **`DB_HOST` debe ser `plates-db-1`, nunca `db`** — ver el incidente real
  de colisión de nombre con la stack "informes" en
  [`ARCHITECTURE.md`](ARCHITECTURE.md#despliegue-docker-compose).
- Si `docker compose up -d --build web` falla con `Conflict: container
  name "/plates-web" is already in use`, es casi siempre un contenedor
  parado que quedó de una corrida anterior: `docker rm -f plates-web` y
  reintentar.
- La red `edge` (`informes_informes_net`) es `external: true` — hay que
  crearla una vez a mano en un entorno nuevo (`docker network create
  informes_informes_net`) si no existe ya por la stack vecina.

## Dozzle (visor de logs web)

Servicio `dozzle` en `docker-compose.yml` — muestra en vivo, desde el
navegador, los mismos logs que `docker logs <contenedor>` daría por SSH.
Necesita un archivo de usuarios que **nunca se commitea** (gitignored,
`dozzle_users.yml`, uno por entorno):

```sh
docker run --rm amir20/dozzle:v11.1.1 generate admin > dozzle_users.yml
# pide la contraseña de forma interactiva (nunca queda en el historial de bash)
docker compose up -d dozzle
```

En producción se accede en `https://placas.ingse.co/dozzle/` (proxied por
el nginx compartido de la stack "informes" — el bloque `location /dozzle/`
vive en `/opt/informes/nginx.conf`, **fuera** de este repo, agregado a
mano igual que el bloque de `cargador.ingse.co`). En local, directo en
`http://127.0.0.1:8081/dozzle/`.

## Testing (`tests/conftest.py`)

- **La base de test es una base separada, `plates_test`**, en el mismo
  Postgres que ya expone Compose en `127.0.0.1:5433` — nunca la base
  `plates` real. `DB_HOST`/`DB_PORT`/`POSTGRES_DB` se **fuerzan** (no
  `setdefault`) a `127.0.0.1`/`5433`/`plates_test` sin importar lo que
  diga el entorno, justamente para que un test nunca pueda apuntar por
  accidente a datos de producción.
- `SECRET_KEY`/`AUTH_USERS` también se fuerzan al importar `conftest.py`,
  antes de cualquier `from app...` — si no, una corrida local heredaría
  las credenciales reales del `.env` y los logins de test fallarían en
  silencio.
- Tres identidades de test ya definidas: `testuser`/`testpass123`
  (admin), `produccionuser`/`produccionpass123` (`production`),
  `puntoventauser`/`puntoventapass123` (`point_of_sale`, Fase 2) — las
  tres son filas reales en la tabla `users` de la base de test desde que
  el login pasó a ser DB-backed, no solo entradas de `AUTH_USERS`/
  `AUTH_ROLES`.
- El fixture `clean_db` hace **`TRUNCATE ... RESTART IDENTITY CASCADE`**
  de 7 tablas (`plates`, `production_orders`, `blank_preparations`,
  `inventory_categories`, `customers`, `users`, `plate_type_prices` — no
  toca `plate_types`, tabla de referencia estática) y vuelve a sembrar
  las 3 identidades de test en `users` antes de cada test que depende de
  él — **no** hay rollback transaccional por test
  todavía. Desde Fase 1d, `db.get_conn()` reusa conexiones de un pool
  (`psycopg_pool.ConnectionPool`) en vez de abrir un socket nuevo por
  llamada, y `app/core/ingestion.py` recibe la conexión como parámetro
  explícito en vez de abrir la suya propia — eso habilita en principio un
  fixture de rollback por test (envolver todo en una transacción y
  revertirla al final), pero todavía no se adoptó (ver el fast-follow del
  paso 10 del plan de Fase 1d); truncar sigue siendo lo que da a cada test
  el mismo estado inicial sin importar qué haya commiteado el anterior.
  Correr pytest en paralelo contra el mismo contenedor de Postgres puede
  generar carreras.
- **Gotcha silencioso**: si Postgres no está corriendo o no es alcanzable
  en `127.0.0.1:5433`, los tests dependientes de BD se **saltan**
  (`pytest.skip`), no fallan. Una corrida "en verde" con muchos `skipped`
  puede estar ocultando que la BD nunca estuvo disponible — vale la pena
  revisar el conteo de skips, no solo que no haya rojos.
- En CI (`test.yml`) el servicio de Postgres se publica explícitamente en
  el puerto `5433` para que este mismo hardcode de `conftest.py` funcione
  igual ahí.

## Variables de entorno

| Variable | Dónde se usa | Nota |
|---|---|---|
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | Compose (`db`), `app/core/db/` | Sin default para la password — nunca hardcodeada en ningún lado. |
| `DB_HOST` | `app/core/db/` | Default de código `"db"` — en Compose se **sobreescribe** a `plates-db-1`. Nunca usar el default en un entorno con la red `edge`. |
| `DB_PORT` | `app/core/db/` | `5432` dentro del contenedor; `5433` es solo el mapeo de host. |
| `DB_POOL_MIN_SIZE` / `DB_POOL_MAX_SIZE` | `app/core/db/__init__.py` | Tamaño del `psycopg_pool.ConnectionPool` por worker de gunicorn. Default `1`/`5` — con 2 workers, máx. 10 conexiones desde la app. |
| `SECRET_KEY` | `app/web/app.py` | Obligatoria — `RuntimeError` al arrancar si falta. |
| `AUTH_USERS` | `app/schema_migrate.py` (backfill único) | `usuario:hash` separados por coma. **Ya no alimenta el login en vivo** (Fase 2) — solo se lee una vez, la primera vez que corre `schema_migrate` contra una base nueva, para crear las filas de `users`. El hash trae `$` → hay que escaparlo como `$$` en `.env` o Compose lo corrompe al interpolar variables (ya documentado en el README raíz). |
| `AUTH_ROLES` | `app/schema_migrate.py` (backfill único) | `usuario:rol` separados por coma, mismo backfill único que `AUTH_USERS`. **No está en `.env.example` hoy** — cualquier usuario sin entrada acá queda `admin` por defecto. |
| `COOKIE_SECURE` | `app/web/app.py` | `1` en producción (HTTPS) para marcar la cookie `Secure`; apagada en dev local sobre HTTP plano. |
| `SENTRY_DSN` | `app/web/app.py` | Opcional — si no está seteada, Sentry ni se inicializa (dev local y tests nunca mandan nada). |
