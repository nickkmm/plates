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
- Tres identidades de test ya definidas: `testuser`/`testpass123` (admin),
  `consultauser`/`consultapass123` (consulta),
  `produccionuser`/`produccionpass123` (producción).
- El fixture `clean_db` hace **`TRUNCATE`** de las 4 tablas antes de cada
  test que depende de él — **no** hay rollback transaccional por test
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
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | Compose (`db`), `app/core/db.py` | Sin default para la password — nunca hardcodeada en ningún lado. |
| `DB_HOST` | `app/core/db.py` | Default de código `"db"` — en Compose se **sobreescribe** a `plates-db-1`. Nunca usar el default en un entorno con la red `edge`. |
| `DB_PORT` | `app/core/db.py` | `5432` dentro del contenedor; `5433` es solo el mapeo de host. |
| `SECRET_KEY` | `app/web/app.py` | Obligatoria — `RuntimeError` al arrancar si falta. |
| `AUTH_USERS` | `app/web/auth.py` | `usuario:hash` separados por coma. El hash trae `$` → hay que escaparlo como `$$` en `.env` o Compose lo corrompe al interpolar variables (ya documentado en el README raíz). |
| `AUTH_ROLES` | `app/web/auth.py` | `usuario:rol` separados por coma. **No está en `.env.example` hoy** — cualquier usuario sin entrada acá es `admin` por defecto. |
| `COOKIE_SECURE` | `app/web/app.py` | `1` en producción (HTTPS) para marcar la cookie `Secure`; apagada en dev local sobre HTTP plano. |
