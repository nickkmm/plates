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
- El fixture `clean_db` (Fase 1d, paso 10) hace un **rollback transaccional
  por test**, no `TRUNCATE`: hace `TRUNCATE ... RESTART IDENTITY CASCADE` +
  siembra de las 3 identidades de test una sola vez al principio, dentro de
  un `SAVEPOINT` que nunca se libera de verdad hasta el final del test — ahí
  se hace `ROLLBACK` real, así que ningún test deja rastro en el `plates_test`
  físico sin importar si pasó o falló. `app.core.db.get_conn` queda
  parcheado (vía `monkeypatch`) para devolver siempre esa MISMA conexión en
  vez de sacar una nueva del pool — así cualquier request HTTP que el test
  dispare (`authenticated_client`, etc.) opera sobre la misma sesión/
  transacción, sin necesitar commits reales para ver sus propios cambios.
  `conn.commit()`/`conn.rollback()` (llamados tanto por el código real de
  `records.py`/`review.py`/`pricing.py`/`inventory.py` como por ~100 sitios
  de test) se reasignan para hacer `RELEASE SAVEPOINT` + `SAVEPOINT` /
  `ROLLBACK TO SAVEPOINT` sobre ESE savepoint en vez de tocar la base real —
  ver el docstring de `clean_db` en `tests/conftest.py` para el detalle
  completo (por qué no se usó `conn.transaction()` de psycopg, cómo se
  recupera una transacción abortada por un error esperado, etc.).
  **Esto NO habilita correr pytest en paralelo contra el mismo
  `plates_test`** — se investigó y descartó explícitamente: dos workers
  compitiendo por las mismas filas/tablas se serializan por los locks de
  Postgres igual que con `TRUNCATE`, paralelismo real necesitaría un
  esquema o base separada por worker (fuera de alcance). El beneficio real
  logrado es velocidad (sin trabajo de disco/WAL por test) y que un test que
  falla a medias nunca deja datos corruptos para el siguiente.
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
