# Plan: captura de órdenes de venta completas (POS) + automatización + cuadre de caja

## Contexto

Hoy la app solo trackea *producción* de placas (herramienta interna, para
consulta): número de orden, fecha, solicitante, tipo/número de placa,
ciudad, cantidad. El usuario quiere expandirla para que también capture la
**orden de venta completa** que llega desde punto de venta — con cliente,
documento, forma de pago, comprobante, precio — porque hoy se pierde toda
esa información. Los objetivos explícitos, en orden:

1. Agregar los campos nuevos al modelo de datos y al flujo de `/ingest`.
2. Automatizar su captura (como ya hace el PDF hoy con order_number/fecha/
   solicitante).
3. Un resumen de fin de día (todo lo ingresado + lo vendido) para
   automatizar el **cuadre de caja** manual que se hace hoy — el motivo
   principal de todo este feature.

Los campos nuevos salen de `data/ENCABEZADO BASE DATOS NUEVO FEAT.xlsx`
cruzado con el formato de PEDIDO real (`data/FORMATO_PEDIDOS_INGSE_2026_v2.xlsx`,
el mismo PDF que `pdf_extract.py` ya parsea hoy). Cada supuesto de este
documento fue verificado contra evidencia real: `pdfplumber` corrido contra
los dos PEDIDO reales en `data/`, y consultas a la base de producción en
vivo. Todas las decisiones de negocio fueron confirmadas por el usuario
campo por campo — nada fue asumido.

## Nomenclatura (leer primero)

**Ver `CLAUDE.md`** (en el repo): toda tabla/columna/identificador de código
va en **inglés**; el contenido que lee o tipea el usuario (UI, textos, y los
*valores* guardados en columnas de vocabulario — formas de pago, tipos de
placa, roles) sigue en **español**.

La Fase 1 se construyó en español antes de fijar esta convención; la Fase 1c
ya renombró todo a inglés **y está en producción**. Todo este documento usa
los nombres finales en inglés.

## Estado actual del repo (verificado contra HEAD, no contra el plan)

**Ya mergeado y desplegado en producción** — no volver a construirlo:

- **Fase 1** (PR #14): tablas `customers`/`users`/`plate_type_prices`/
  `schema_migrations`, columnas POS nuevas en `production_orders`/`plates`,
  índice `plates_order_id_idx`, runner `app/schema_migrate.py`,
  `app/migrations/0001_pos_fields.sql`, backfill de clientes/usuarios.
- **Fase 1b** (PR #16): tabla `plate_types` + FKs reales
  (`plates.plate_type`, `plate_type_prices.plate_type`,
  `blank_preparations.category`), `app/migrations/0002_reference_integrity.sql`.
- **Fase 1c** (PR #17): rename completo a inglés,
  `app/migrations/0003_rename_to_english.sql`. El mapa completo de renames
  vive en ese archivo y en `docs/DATABASE.md` — no se repite acá.
- **Fase 6** (PR #13): limpieza de emojis/copy decorativo en las plantillas
  existentes. Confirmado con grep: hoy no queda ningún emoji en
  `app/web/templates/`.
- El paso `docker compose exec -T web python -m app.schema_migrate` **ya
  está** en `.github/workflows/deploy.yml`, justo después de
  `docker compose up -d --build`.
- `init.sql` y `docs/DATABASE.md` ya reflejan el estado final de 1/1b/1c.

**Pendiente de construir**: Fase 1d (refactor de arquitectura), Fase 1e
(monitoreo), Fase 2 en adelante (el feature POS en sí).

Números de referencia verificados en HEAD (`wc -l` / `grep`), para que un
implementador no trabaje contra cifras viejas:

| Archivo | Líneas | Nota |
|---|---|---|
| `app/web/app.py` | 687 | 19 rutas |
| `app/core/db.py` | 556 | 24 funciones (incl. `get_conn`) |
| `app/core/process.py` | 237 | `conn.commit()` en 141 y 235 |
| `app/web/auth.py` | 148 | no se restructura |
| `app/core/normalize.py` | 341 | sin los vocabularios nuevos todavía |
| `tests/web/test_app.py` | 1672 | |
| `tests/core/test_db.py` | 855 | ~189 llamadas `db.<func>(` |
| `tests/core/test_process.py` | 517 | ~35 llamadas a process_rows/replace_order |
| suite completa | 512 tests | |

## ⚠️ Preguntas menores sin resolver — defaults razonables, no bloquean

- **B4**: "todo lo ingresado" en el resumen de fin de día — ¿se refiere al
  alistamiento del día (`blank_preparations`) o a las órdenes cargadas ese
  día? Default: las órdenes (ya cubierto por la query de cuadre de caja).
- **B5**: si se edita el nombre/datos de un cliente desde una orden ya
  guardada, eso cambia el histórico compartido de TODAS sus órdenes.
  Default recomendado: `record_save` solo puede cambiar *a qué cliente*
  apunta la orden (re-resolver `customer_id`), no editar los atributos del
  cliente en sí — eso queda para una futura pantalla de clientes dedicada,
  fuera del alcance de este feature.

## Diseño de datos final

> Las tablas y columnas de esta sección **ya existen en producción**
> (Fases 1/1b/1c). Se documentan acá porque las Fases 2-5 se construyen
> sobre ellas; el schema autoritativo es `init.sql` + `docs/DATABASE.md`.

### Tabla `customers`

`requested_by` (el solicitante — PTO.V, MUVA, etc.) y el "cliente" del
Encabezado son **la misma entidad**, no dos cosas distintas.

```sql
customers
  id                SERIAL PRIMARY KEY
  name              TEXT NOT NULL UNIQUE   -- canonical: normalize_requested_by(value)
  type              TEXT                    -- normalize.CUSTOMER_TYPES
  document_type     TEXT                    -- normalize.DOCUMENT_TYPES
  document_number   TEXT                    -- always TEXT: a NIT carries check digits/dashes
  document_relation TEXT                    -- ONLY if document_type = CÉDULA
  phone             TEXT
  email             TEXT
  transit_license   TEXT                    -- ONLY if type = PERSONA NATURAL
  created_at        TIMESTAMP DEFAULT now()
```

(Los *valores* de `type`/`document_type`/`document_relation` son términos de
negocio en español — `PERSONA NATURAL`, `CÉDULA`, `DUEÑO` — solo los nombres
de tabla/columna son inglés, per `CLAUDE.md`.)

`UNIQUE` en `name` es la única restricción de unicidad del schema fuera de
PKs — es lo que hace que "buscar cliente por nombre y reusar sus datos"
tenga una definición clara, y da el índice de búsqueda gratis. **Política de
colisión**: dos clientes reales distintos que tipean el mismo nombre
canónico se tratan como el MISMO cliente — es el comportamiento de hoy
(`normalize._REQUESTED_BY_MAP_RAW` ya deja variantes como `MUVA GRANADA`/
`MUVA SERGIO` sin fusionar a propósito); para separarlos se tipea un nombre
calificado (`PTO.V FABIO M`). No hace falta ningún mecanismo nuevo.

- **`production_orders.requested_by` (texto) SE MANTIENE, `NOT NULL`, y se
  sigue escribiendo siempre** — es una copia desnormalizada de
  `customers.name`, y sigue siendo la fuente que usan **8 queries de lectura
  de `db.py`** (`recent_orders`, `find_orders_by_number`,
  `search_orders_by_number`, `search_plates_by_number`,
  `find_orders_for_plate`, `review_rows`, `order_detail`,
  `distinct_requested_by`) **y las 5 plantillas que lo muestran o editan**
  (`_orders_table.html`, `record_detail.html`, `review.html`, `records.html`,
  `ingest.html`) — nada de eso lo toca este feature.
  **Regla: toda escritura que fija `customer_id`
  también fija `requested_by = customers.name` en la misma operación.**
  Ya documentado como rareza en `docs/DATABASE.md`.
- `customer_id` en `production_orders` es **nullable** (el backfill dejó 1
  orden sin resolver; ver Fases ejecutadas).
- En `/ingest`, si el nombre tipeado matchea `customers.name` (lookup por
  `normalize_requested_by(tipeado)`), **se reutilizan automáticamente** sus
  datos (tipo/documento/teléfono/correo/licencia de tránsito). Si no
  matchea, se trata como cliente nuevo.
- Campos condicionales (`document_relation` solo si CÉDULA;
  `transit_license` solo si PERSONA NATURAL): en el HTML van con `disabled`
  (no `readonly` — `readOnly` no tiene efecto sobre `<select>`, a diferencia
  del truco que ya usa `line_type` con inputs de texto). Un campo `disabled`
  no se envía en el POST, lo cual es correcto acá porque el valor debe
  quedar en `NULL`. **La regla se refuerza también en el servidor** (nunca
  confiar solo en el HTML): forzar `document_relation = NULL` si
  `document_type != 'CÉDULA'`, y `transit_license = NULL` si
  `type != 'PERSONA NATURAL'`.

### Columnas POS en `production_orders` (una por orden)

`receipt_type`, `receipt_number`, `customer_id` (FK → `customers.id`),
`payment_method`, `tracking_number`, `client_order_number`, `delivery_date`,
`payment_date`, `dispatched_by`, `received_by`, `notes`, `created_by`
(FK → `users.id`).

Mapeo con el Excel/PDF original, para trazabilidad: `receipt_type`/
`receipt_number` = `COMPROBANTE`; `payment_method` = `FORMAS DE PAGO`/
`FORMA DE PAGO`; `tracking_number` = `GUÍA #`; `client_order_number` =
`PEDIDO #` (el número de reclamo de cara al cliente, distinto del
`order_number` real); `delivery_date` = `FECHA ENTREGA`; `payment_date` =
`FECHA TRANSF./PAGO`; `dispatched_by` = `RESPONSABLE DESPACHO`;
`received_by` = `NOMBRE QUIEN RECIBE EL PEDIDO`; `notes` = `OBSERVACIONES`.

Se descarta el campo `TRÁNSITO:` del header del PDF — redundante con el
nombre del cliente y rara vez se llenaba. No se mapea a ningún campo.
(Nota: `TRÁNSITO` también aparece como uno de los *valores* posibles de
`customers.type` — coincidencia de nombre, sin relación con esta decisión.)

### Columnas POS en `plates` (por placa/línea, no por orden)

`unit_price NUMERIC(12,2)`, `reason` (deterioro/pérdida/otros — mapea a
`MOTIVO`/`FABRICADA POR` del Excel), `vehicle_brand` (mapea a
`MARCA VEHICULO/MOTO`).

`reason` cubre el `FABRICADA POR` del Excel borrador: **no es un campo
separado y no tiene ninguna relación con `prepared_by`** (que es quién
alistó el blank, otra cosa).

`reason` y `vehicle_brand` son datos del vehículo, no de la orden — una
misma orden puede cubrir varias placas de vehículos distintos, así que van
en `plates`. (`transit_license` NO va acá — es un dato del *cliente*
persona natural.) Ninguno de los dos tiene campo impreso en la tabla de
líneas del PDF (esa tabla solo tiene ITEM/PLACA/CIUDAD/CANT./tipo/rango/
VALOR PLACA/VALOR TOTAL) — **siempre son de captura manual**.

**Regla de re-snapshot de `unit_price`** (una regla, tres lugares donde se
aplica):
- Placa nueva (`/ingest`, PDF, o "+ agregar placa" en `record_save`) →
  toma la tarifa vigente para su `plate_type` en ese momento.
- Placa existente editada y el `plate_type` **no cambia** → `unit_price`
  NO se toca, sin importar si cambia `quantity` (el total se recalcula
  solo).
- Placa existente editada y el `plate_type` **sí cambia** → se vuelve a
  tomar la tarifa vigente del tipo nuevo.

Aplica tanto a `db.update_plate_full` (detalle de Registro) como a
`db.update_plate` (cola de `/review`, que también permite cambiar
`plate_type`) — ambas necesitan leer el `plate_type` viejo antes del
`UPDATE` para saber si cambió.

### Tabla `plate_type_prices` (tarifas, editable por admin)

```sql
plate_type_prices
  plate_type TEXT PRIMARY KEY REFERENCES plate_types(plate_type)
  unit_price NUMERIC(12,2) NOT NULL
  updated_at TIMESTAMP DEFAULT now()
  updated_by TEXT
```

**Ojo con los dos "espacios de nombres" que se parecen** (ver
`docs/DATABASE.md`): `plates.plate_type` (7 valores granulares) vs.
`inventory_categories.category` (3 materiales). Esta tabla va sobre los 7
tipos, porque el precio se factura por tipo de placa vendida, no por
material de inventario — confundirlos dejaría `ORNAMENTAL` sin poder tener
precio y le daría a `REMOLQUE` el precio de `CARRO PÚBLICO`. Desde la Fase
1b hay **FK real** a `plate_types`; la validación en Python contra
`normalize.PLATE_TYPES` se mantiene igual (red de seguridad doble, con mejor
mensaje de error antes de llegar a la BD).

**No se siembra en `init.sql`** — la tabla arranca vacía. `/precios` muestra
una fila por cada entrada de `PLATE_TYPES` (leída de la constante en
código), marcando "sin tarifa" la que no tenga fila todavía. Así la UI nunca
depende de datos sembrados y el `TRUNCATE` de los tests no rompe nada. Una
placa sin tarifa configurada guarda `unit_price = NULL` y dispara un aviso
en `/cuadre` (nunca un 0 silencioso).

**Es un valor SUGERIDO, no bloqueado**: el precio real varía por cliente y
por descuentos negociados — eso explica, sin necesidad de adivinar, la
diferencia de precios entre los dos PEDIDO de muestra (negociación distinta,
no un error de captura). En `/ingest`, al elegir el
`plate_type` de una línea, el campo de precio se pre-llena con esta tarifa
vía JS, pero **queda editable**. `plates.unit_price` guarda lo que quedó
tipeado en ese momento — no una referencia viva a `plate_type_prices`, así
que si la tarifa cambia después, las órdenes viejas no se alteran. El total
es siempre `unit_price × quantity`, calculado al leer, nunca guardado; si un
caso puntual necesita otro total, se ajusta el `unit_price` de esa línea.

**Precio y `recalc_inventory` son cosas separadas y deben seguir
separadas**: `recalc_inventory`/`_consumed_by_material`/`inventory_view`
agregan CANTIDADES por MATERIAL (3 categorías); el precio agrega DINERO por
TIPO (7 valores). No "unificar" nunca estos dos loops.

### Tabla `users` (RBAC real, reemplaza los env vars)

```sql
users
  id SERIAL PRIMARY KEY
  username TEXT UNIQUE NOT NULL
  password_hash TEXT NOT NULL   -- mismo formato werkzeug de hoy (scrypt/pbkdf2)
  role TEXT NOT NULL            -- valor en español, ver nota de acentos abajo
  active BOOLEAN DEFAULT true
  created_at TIMESTAMP DEFAULT now()
```

- El backfill (ya corrido) creó un usuario por cada entrada de
  `AUTH_USERS`/`AUTH_ROLES`, con los mismos hashes — no hay que resetear
  contraseñas de nadie.
- **Pendiente (Fase 2)**: `app/web/auth.py` pasa de leer los env vars a
  consultar `users`. **Riesgo a controlar**: el login pasa a depender de la
  BD (hoy funciona incluso si la BD está caída) — aceptable dado que el
  resto de la app ya depende de la BD para todo lo demás.
- ⚠️ **Acentos en los valores de rol — verificar antes de escribir código**:
  el valor literal en el código y en `AUTH_ROLES` hoy es **`produccion` sin
  tilde** (`auth.py:30` → `ROLE_PRODUCCION = "produccion"`; `tests/conftest.py:55`
  → `AUTH_ROLES=...:produccion`; comentario de `init.sql` → `admin /
  produccion / consulta / punto_venta`). La prosa de `docs/WEB.md`,
  `docs/README.md` y de este plan escribe "producción" con tilde porque es
  español corriente, pero **el valor guardado y comparado es sin tilde**.
  Al armar la tupla `ROLES` y al leer/escribir `users.role`, usar
  exactamente los literales que ya existen: `"admin"`, `"produccion"`,
  `"consulta"`, `"punto_venta"`. Cambiarlos rompería el login de los
  usuarios existentes.
- **Rol nuevo `punto_venta` (PTO.V) — permiso acotado**: hoy `auth.py` solo
  tiene dos niveles (`FULL_ACCESS_ROLES = {admin}` vía `@login_required`, y
  "cualquier rol autenticado" vía `@consulta_allowed`). Hace falta un tercer
  nivel intermedio:
  - `POS_ROLES = {"admin", "punto_venta"}` + decorador nuevo `@pos_allowed`,
    aplicado a: `/ingest` (GET/POST), `/ingest/pdf` (GET/POST),
    `/records/<id>/save`, y `/cuadre` (ver + exportar PDF).
  - `@login_required` (sigue siendo admin-only, sin cambios de
    comportamiento) se queda en: `/review/*`, `/inventory/alistado`,
    `/records/delete`, `/precios`.
  - `@consulta_allowed` no cambia — `punto_venta` cae ahí también para
    lectura (`/records`, `/records/<id>` en modo lectura), ya que ese
    decorador deja pasar "cualquier rol autenticado" por diseño.
  - El rol `produccion` existente **no cambia** — sigue siendo de solo
    lectura, igual que `consulta`.
- `users.role` es texto libre validado contra una tupla `ROLES` en
  `auth.py` (mismo patrón que `normalize.PLATE_TYPES`), no un `CHECK` en la
  BD.
- `production_orders.created_by` (FK → `users.id`): el usuario logueado que
  le dio "Guardar" a la orden (distinto de `dispatched_by`/`received_by`,
  que son datos de la entrega física). `process_rows`/`replace_order` hoy no
  reciben ninguna noción de "quién llama" — ganan un parámetro
  `created_by: int | None` que la ruta pasa desde `session["user_id"]` (el
  login ahora guarda el id numérico en sesión, no solo el username).

### Runner de migraciones (`app/schema_migrate.py`) — ya en producción

```sql
schema_migrations
  version TEXT PRIMARY KEY
  applied_at TIMESTAMP DEFAULT now()
```

Runner: `python -m app.schema_migrate`. El nombre es a propósito distinto de
`app/migrate.py`, que ya existe y hace algo completamente distinto
(migración one-off del Excel histórico, se invoca contra producción);
confundir los dos comandos sería un error muy caro.

Reglas que cualquier migración futura debe respetar:
- Los archivos `.sql` van en `app/migrations/NNNN_nombre.sql`, **no** en un
  `migrations/` en la raíz — `web.Dockerfile` solo hace `COPY app/ ./app/`,
  así que cualquier cosa fuera de `app/` no llega a la imagen.
- Cada archivo se aplica **y** se registra en `schema_migrations` dentro de
  la misma transacción (un archivo a medio aplicar nunca queda marcado como
  completo). Correr el runner dos veces debe ser un no-op.
- Los backfills en Python se registran bajo versiones sintéticas
  (`BACKFILL_CLIENTES_VERSION = "0002_backfill_clientes"`,
  `BACKFILL_USUARIOS_VERSION = "0003_backfill_usuarios"`). ⚠️ **Esos
  *strings* ya están escritos en la tabla `schema_migrations` de producción:
  cambiar su valor haría que el runner crea que el backfill nunca corrió y
  lo vuelva a ejecutar contra datos reales.** Los nombres de las constantes
  y funciones de Python sí se pueden renombrar (ver Fase 1d, paso 4); los
  valores string, no.
- El runner ya corre en el deploy: `.github/workflows/deploy.yml` ejecuta
  `docker compose exec -T web python -m app.schema_migrate` justo después
  de `docker compose up -d --build`.

## Cuadre de caja

- **Reporte calculado en vivo** (no un "cierre" que se guarda y bloquea) —
  ruta `/cuadre?desde=YYYY-MM-DD&hasta=YYYY-MM-DD`, ambos parámetros
  default hoy (usar `_today_iso()` con zona horaria Bogotá, que ya existe
  en `app.py:86` — nunca `date.today()` crudo, el contenedor corre en UTC).
- **Acceso**: `@pos_allowed` (`admin` + `punto_venta`).
- **Exportar a PDF**: botón/ruta para descargar los mismos totales en un PDF
  simple, para que PTO.V se lo lleve impreso al admin. **Confirmado: se usa
  `reportlab`** (ya probado en el proyecto para generar los PDFs sintéticos
  de test, confiable, alcanza para una tabla de totales) — se promueve de
  `requirements-dev.txt` (`reportlab==5.0.0`) a `requirements.txt` como
  dependencia real de producción en esta fase.
- Se agrupa **siempre por `order_date`** (no por fecha de pago/
  transferencia — más simple).
- Se agrupa por `payment_method`, con un bucket explícito **"SIN FORMA DE
  PAGO"** para `NULL` (todas las órdenes históricas tienen
  `payment_method = NULL` — sin este bucket, un `GROUP BY` las esconde o las
  descarta en silencio). El bucket es texto en español porque es una
  etiqueta que ve el usuario.
- Excluye `is_voided` (nunca se produjo) y `is_plate_annulled` (blank
  perdido, no se cobró).
- Una placa con `unit_price IS NULL` genera un aviso explícito con su
  `plate_type`/`order_number` — **nunca** `COALESCE(unit_price, 0)`.

Query de referencia (dinero en `plates`, forma de pago en
`production_orders`):

```sql
SELECT o.order_date,
       COALESCE(o.payment_method, 'SIN FORMA DE PAGO') AS payment_method,
       count(*)                                        AS n_plates,
       COALESCE(SUM(p.quantity), 0)                    AS total_qty,
       SUM(p.unit_price * p.quantity)                   AS total,
       count(*) FILTER (WHERE p.unit_price IS NULL)     AS missing_price_count
FROM plates p
JOIN production_orders o ON o.id = p.order_id
WHERE o.order_date BETWEEN %s AND %s
  AND p.is_voided = false
  AND p.is_plate_annulled = false
GROUP BY o.order_date, COALESCE(o.payment_method, 'SIN FORMA DE PAGO')
ORDER BY o.order_date, 2
```

`unit_price`/`total` vuelven de psycopg como `decimal.Decimal` — mantener
`Decimal` de punta a punta (nunca mezclar con `float`); agregar un filtro
Jinja para formato colombiano (`$1.954.000`), algo que la app no tiene hoy.

Como `FORMA DE PAGO:` viene en blanco en los dos PEDIDO reales de muestra, y
es el campo clave del cuadre, en `/ingest` debe ser un `<select>`
**obligatorio** (sin opción en blanco preseleccionada, con un `OTROS`
explícito para casos genuinamente desconocidos) — si viene vacío desde un
PDF, mostrar el mismo tipo de aviso que ya usa `_form_from_pedido` para
otros campos ambiguos ("El PDF no indica la forma de pago — elegila antes de
guardar.").

## Fases 1 / 1b / 1c — ya ejecutadas (registro)

Resumen de lo que se hizo, para contexto. **No hay nada que ejecutar acá.**

- **Fase 1** creó el schema POS y el runner de migraciones. El backfill de
  clientes agrupó `SELECT DISTINCT requested_by FROM production_orders` por
  `normalize.normalize_requested_by(valor)` (la clave de agrupación es el
  **canónico**, nunca el string crudo), descartó los valores sin ningún
  caracter alfanumérico (`'//'`), insertó un `customers` por grupo con
  atributos NULL, y apuntó `customer_id` de cada orden a su grupo **sin
  reescribir `requested_by`**. Resultado real en producción: **190
  clientes, 1 orden con `customer_id IS NULL`** (el `'//'`, dejado para
  revisión manual, no como cliente basura). El algoritmo vive en
  `app/schema_migrate.py` (función `backfill_clientes` hoy; pasa a
  `backfill_customers` en la Fase 1d, paso 4).
- **Fase 1b** agregó `plate_types` (sembrada con los 7 valores de
  `normalize.PLATE_TYPES`) y las FKs reales de `plates.plate_type`,
  `plate_type_prices.plate_type` y `blank_preparations.category`. Se
  verificó contra producción que los datos ya estaban limpios (0 filas
  huérfanas en ambos casos), así que no hizo falta limpiar nada primero.
  La validación en Python se mantuvo — la FK es red de seguridad adicional,
  no reemplazo.
- **Fase 1c** renombró a inglés las 2 tablas y las 24 columnas que la Fase 1
  había creado en español. Mapa completo en
  `app/migrations/0003_rename_to_english.sql` y en `docs/DATABASE.md`. Las 2
  FKs existentes siguieron funcionando tras el rename (Postgres las trackea
  por OID, no por nombre).

**Invariante que sigue vigente para toda migración futura**: una BD nueva
armada desde `init.sql` y una BD vieja migrada con `app/migrations/*.sql`
deben terminar con el mismo schema exacto (comparar
`information_schema.columns` + `table_constraints` de ambas). Y antes de
tocar producción: `pg_dump` de respaldo y correr la migración primero contra
una copia restaurada en local (no hay ambiente de staging).

`tests/conftest.py` ya está adaptado: `clean_db` trunca las tablas nuevas, y
`_db_ready` corre siempre el runner de migraciones después del chequeo de
`to_regclass('public.plates')`, para converger un DB nuevo o viejo al mismo
schema.

## Fase 1d / 1e / 2 — ya ejecutadas (registro, 2026-09-20)

**No hay nada que ejecutar acá tampoco.** Resumen para que un agente que
implemente Fase 3 en adelante no vuelva a tocar esto:

- **Fase 1d** (PR #19, mergeado): refactor completo por dominios. `db.py`
  → paquete `app/core/db/{orders,plates,inventory}.py` con re-exports
  explícitos en `__init__.py`; `process.py` → `app/core/ingestion.py`
  (`_is_alistado` → `is_alistado`, pública); `app/web/app.py` (687 líneas)
  → 5 blueprints en `app/web/blueprints/` (`ingest.py`, `records.py`,
  `review.py`, `inventory.py`, `auth_routes.py`) + `app.py` reducido a 83
  líneas; `app/web/helpers.py` nuevo; pool de conexiones real
  (`psycopg_pool.ConnectionPool`, perezoso, `DB_POOL_MIN_SIZE`/
  `DB_POOL_MAX_SIZE`); `conn` explícito en las 3 funciones de
  `ingestion.py` (arregla el bug real de transacciones no atómicas);
  `row_factory=dict_row`; `psycopg.sql.SQL` para la query de resumen de
  orden (`OrderSummary`, dataclass tipado); deduplicación de
  `is_real_plate(...) and not is_voided` y del bloque de contadores.
  Steps 10 (reorg de tests + fixture de rollback) y 11 (reescritura de
  docs) del plan original de esta fase se hicieron/actualizaron aparte
  (docs) o quedaron como fast-follow no bloqueante. **Step 10, mitad de
  reorg de tests: HECHO (2026-09-21)** -- `tests/web/test_app.py` y
  `tests/core/test_db.py` quedaron divididos siguiendo el mismo espejo que
  el código fuente (`tests/web/test_login.py`/`test_review.py`/
  `test_inventory.py`/`test_records.py`/`test_ingest.py`/`test_roles.py` y
  el paquete nuevo `tests/core/db/` con `test_connection.py`/
  `test_orders.py`/`test_plates.py`/`test_inventory.py`/`test_customers.py`/
  `test_users.py`/`test_pricing.py`), cero cambio de comportamiento (789
  tests, antes y después). **Step 10 completo (2026-09-21)**: la otra
  mitad -- convertir `clean_db` de `TRUNCATE` a una transacción con
  rollback por test -- también está hecha (`tests/conftest.py::clean_db`),
  vía `SAVEPOINT`/`RELEASE SAVEPOINT`/`ROLLBACK TO SAVEPOINT` a mano (no
  `conn.transaction()` de psycopg -- ese prohíbe los `conn.commit()`/
  `.rollback()` explícitos que el código real y ~100 tests ya hacían).
  Investigado y descartado explícitamente: esto NO habilita correr pytest
  en paralelo contra el mismo `plates_test` (dos workers competirían por
  los mismos locks de Postgres igual que con `TRUNCATE`; paralelismo real
  necesitaría esquema/base separada por worker, fuera de alcance) -- el
  beneficio real es velocidad y que un test que falla a medias no deja
  datos corruptos para el siguiente. Detalle completo en el docstring de
  `clean_db`.
- **Fase 1e** (PR #18 + PR #20, mergeados): rotación de logs de Docker
  (`json-file`, `max-size: 10m`, `max-file: 3`) en `db`/`web`; Sentry
  (`sentry-sdk[flask]`) inicializado en `app.py` solo si `SENTRY_DSN` está
  seteada — **la variable todavía no está seteada en producción** (pendiente
  de que el usuario cree una cuenta de Sentry y pase el DSN como secreto).
  UptimeRobot (externo, ping a `/healthz`) — **pendiente de configurar por
  el usuario**, no requiere código.
- **Fase 2** (PR #21, mergeado): `models.py` (~15 campos POS opcionales en
  `ExtractedRow`, default `None`, con validador `mode="before"` para
  `delivery_date`/`payment_date`); `normalize.py` (`RECEIPT_TYPES`,
  `CUSTOMER_TYPES`, `DOCUMENT_TYPES`, `DOCUMENT_RELATIONS`,
  `PAYMENT_METHODS`, `REASONS` + `normalize_choice()`); `db/customers.py`
  nuevo (`resolve_or_create_customer`, `get_customer`,
  `get_customer_by_name`); `db/users.py` nuevo (`get_user_by_username`,
  `get_user_by_id`, `create_user`, `list_users`); `db/pricing.py` nuevo
  (`current_prices`, `price_list`, `set_price`); `db/orders.py` — 
  `insert_order` ahora toma la `ExtractedRow` completa + `customer_id` +
  `created_by`; `update_order` gana `customer_id` (con sentinel `_UNSET`
  para no pisarlo por accidente desde llamadores viejos — importante para
  Fase 3: `record_save` today NO pasa `customer_id`, así que sigue sin
  tocarlo hasta que Fase 3 lo actualice a propósito); `db/plates.py` —
  regla de re-snapshot de `unit_price` implementada en
  `_resnapshot_unit_price()` (usada por `update_plate`/`update_plate_full`);
  `ingestion.py` — resuelve/crea cliente por grupo, snapshotea
  `current_prices` antes de cada `insert_plate` (solo si la fila no trae
  ya un `unit_price`), acepta `created_by: int | None`, generaliza el
  chequeo de "todas las filas del grupo coinciden" a
  `_ORDER_LEVEL_FIELDS` (10 campos, excluye `reason`/`vehicle_brand`/
  `unit_price` que sí son por línea), y corrige el bug real de
  ALISTAMIENTO usando `plate_type` directo como `category` (ahora pasa por
  `normalize.inventory_category()`, y lanza si el tipo es `ORNAMENTAL` —
  no tiene material). **`auth.py` ahora es DB-backed**: `authenticate()`
  consulta `users` (vía `db/users.py`) en vez de `AUTH_USERS`/
  `AUTH_ROLES`; `login_post` guarda `session["user_id"]`; nuevo rol
  `punto_venta` (`ROLE_PUNTO_VENTA = "punto_venta"`, sin tilde/con guión
  bajo) + `POS_ROLES = {admin, punto_venta}` + decorador `@pos_allowed` —
  **ningún blueprint usa `@pos_allowed` todavía, eso es trabajo de Fase 3**.
  `load_users()`/`load_roles()` se mantienen SOLO para el backfill histórico
  de `schema_migrate.py`, ya no los usa el login en vivo. Verificado en
  producción: los 3 usuarios reales (`admin`/`ptov`/`produccion`) siguen en
  `users` con sus hashes intactos; login rechaza credenciales inválidas con
  401 limpio (no 500) contra la BD real.

**Para quien implemente Fase 3 en adelante**: los nombres de módulo/función
que aparecen en las secciones de Fase 3-7 más abajo YA reflejan esta
estructura nueva (`app/core/db/orders.py`, `app/core/ingestion.py`,
`app/web/blueprints/ingest.py`, etc.) — no hay que traducir nada, se puede
implementar tal cual está escrito.

## Fase 3 / 5a — ya ejecutadas (registro, 2026-09-20)

- **Fase 3** (PR #23, mergeado): `/ingest` y `/ingest/pdf` ahora
  `@pos_allowed` (antes `@login_required`) — `punto_venta` ya puede usarlos
  como su punto de entrada diario (`landing_url()` lo manda ahí). Nuevo
  fieldset de orden completo (comprobante/cliente/forma de pago obligatoria/
  guía#/pedido#/fechas/responsable/observaciones) + columnas por línea
  `reason`/`vehicle_brand`/`unit_price` (pre-llenado por JS desde
  `plate_type_prices`, editable). Autocompletado de cliente: la lista
  completa de `customers` se embebe como JSON en `ingest.html`
  (`_customers_context`, mismo patrón que `distinct_requested_by`/
  `distinct_cities` — esta app no tiene endpoints AJAX) y el JS auto-llena
  el fieldset de cliente cuando el nombre tipeado matchea. Las 3 copias
  independientes del dict "sticky" se unificaron en
  `_sticky_header_from_form(form)` (única fuente, usada también por
  `_form_from_pedido`) — la clave completa de 20 campos vive en la
  constante `_STICKY_HEADER_FIELDS` de `app/web/blueprints/ingest.py`, que
  cualquier trabajo de Fase 4 (PDF) debe reusar tal cual, no reinventar.
  `resolve_or_create_customer` ganó kwargs de atributos, **solo escritos en
  la rama INSERT** (cliente genuinamente nuevo) — la rama
  `ON CONFLICT` sigue sin tocar atributos de un cliente existente, por B5.
  Paridad completa en `record_detail.html`/`record_save`
  (`@pos_allowed` también en `/records/<id>/save`): re-resuelve
  `customer_id` desde `requested_by` en cada guardado, pero nunca reescribe
  los atributos propios del cliente (B5, confirmado). `unit_price` de una
  placa editada sigue sin ser un parámetro expuesto en ningún form — la
  regla de re-snapshot en `db/plates.py` es el único camino que la fija.
  `format_cop` (nuevo, `app/web/helpers.py`) es el filtro Jinja `| cop` para
  moneda colombiana.
- **Fase 5a** (PR #22, mergeado): `app/web/blueprints/pricing.py` nuevo,
  `GET/POST /precios`, `@login_required` (admin-only, nunca `@pos_allowed`
  — `punto_venta` no edita tarifas). Tab "Precios" en `_nav.html`
  (gateada por `full_access_roles`, no `pos_roles`).

**Para quien implemente Fase 4 en adelante**: el diccionario `header` que
produce `/ingest` (tanto a mano como desde un PDF) tiene EXACTAMENTE las 20
claves de `_STICKY_HEADER_FIELDS` en `app/web/blueprints/ingest.py` — Fase 4
debe mapear las etiquetas nuevas del pie del PDF hacia esas claves
(`customer_type`/`customer_document_type`/`customer_document_number`/
`customer_document_relation`/`customer_phone`/`customer_email`/
`customer_transit_license` para el cliente; el resto tal como ya describía
este plan). `_form_from_pedido` hoy deja todas esas claves en blanco salvo
`order_date`/`order_number`/`requested_by` — es el punto exacto que Fase 4
extiende.

---

## Fase 1d — Refactor de arquitectura por dominios (antes de Fase 2)

Pedido explícito del usuario: "quiero una arqui más robusta con más
separación de responsabilidades siguiendo principios SOLID... se me hace muy
duro seguir la trazabilidad... sin hacer sobreingeniería."

Hoy `app/web/app.py` tiene **687 líneas con 19 rutas** todas juntas, y
`app/core/db.py` **556 líneas con 24 funciones** de `production_orders`/
`plates`/inventario mezcladas. La Fase 2 en adelante agrega 4 dominios más
(`customers`, `users`, `pricing`, cuadre de caja) — sin este refactor, ambos
archivos pasarían de 1000 líneas.

**Principio (decisión explícita, no lo obvio)**: separar por **dominio de
negocio dentro de las dos capas que YA existen** (web ↔ core), **no**
agregar una capa nueva tipo services/repositories.

### Sin ORM, sin framework de DI, sin capa repository/Protocol

**Sin ORM**: esta app es de lectura pesada, con queries de reporte
(`array_agg ... FILTER`, `string_agg(CASE ...)`, `count(*) FILTER`) y una
superficie de escritura chica — un ORM es peor, no mejor, justo en ese tipo
de queries.

**Sin interfaces/`Protocol`** para invertir la dependencia de `ingestion.py`
hacia `db.py`, por tres razones:

1. La única ganancia real de legibilidad sería no repetir `conn` en cada
   llamada — el resto del cuerpo queda igual. El valor de "documentar el
   contrato" ya lo dan mejor los docstrings que `db.py` tiene hoy
   (`recent_orders` explica por qué ordena por `order_date`, `delete_plate`
   explica la regla de limpiar flags).
2. En Python, `Protocol` es estructural (duck typing): no hay declaración
   explícita de "esto implementa aquello", así que "ir a la definición" no
   sigue el link. Eso hace la trazabilidad **peor**, justo lo contrario de
   lo que busca este refactor.
3. **Decisiva**: el motivo de pasar `conn` explícito es dejar visible en la
   firma quién es dueño de la transacción/commit. Una interfaz abstracta
   tendría que esconder `commit()` (mintiendo sobre ser "agnóstica de
   storage") o agregar una abstracción "unit of work" para resolver el
   problema que la primera creó.

**Sin framework de DI**, pero ojo: "los tests pegan contra Postgres real"
no prueba que no haya un problema de inversión de dependencias — sí lo hay,
y es el bug de transacciones de la mejora 3. Lo que sí es cierto es que no
hacen falta mocks: reglas de negocio reales viven a propósito en el SQL de
`db.py` (ej. `recalc_inventory` itera categorías fijas justo por un bug real
que ya se encontró una vez, ver `docs/DATABASE.md`), así que un doble falso
en memoria habría repetido el mismo modelo mental equivocado y pasado en
verde igual.

**Señales que sí ameritarían reabrir esto** (ninguna es "el equipo creció"):
(a) aparece una segunda implementación real y nombrable (un segundo backend
concreto ya planeado, no "tal vez cambiar de BD algún día"); (b)
`ingestion.py` deja de ser el único escritor de lógica de negocio y la
superficie de funciones de `db.py` que consume cruza ~15; (c) la suite se
vuelve tan lenta que el equipo empieza a saltearla — y ahí la solución es un
fixture con rollback transaccional (que el paso 7 habilita y el paso 10
implementa), no dobles falsos.

**También descartado**: Alembic (el runner propio ya está en producción con
versiones reales grabadas — migrar ahora sería puro churn; revisar solo si
el dolor futuro es específicamente en migraciones, no en queries);
`create_app()` factory (ver abajo); y `StrictUndefined` de Jinja — se
verificó que **hoy rompería**: `records_search_pdf` renderiza `records.html`
sin `pdf_results`/`pdf_filename` en sus tres rutas de error, y `records()`
tampoco los pasa. Revisar recién después de separar en blueprints, cuando
cada uno sea chico para auditar.

### Las 6 mejoras concretas de esta fase

1. **`row_factory=dict_row`** (de `psycopg.rows`, ya viene con la librería,
   cero dependencias nuevas) en `get_conn()` — borra las **8** repeticiones
   de `dict(zip(cols, r, strict=False))` + los **8** `assert cur.description
   is not None` en `db.py` (~50 líneas). Hacer **antes** de separar `db.py`
   en paquete, para dividir código ya limpio.
2. **`psycopg.sql.SQL`** para componer la query de resumen de orden que hoy
   está copiada 3 veces casi idéntica (`recent_orders` db.py:251,
   `find_orders_by_number` db.py:294, `search_orders_by_number` db.py:369 —
   sus propios docstrings dicen "same row shape as recent_orders()") — un
   solo `_ORDER_SUMMARY_SELECT` + `WHERE` compuesto por caso. De paso
   elimina el único SQL armado con f-string del proyecto (`db.py:149`,
   `_consumed_by_material` — no es una inyección hoy porque los llamadores
   pasan constantes literales, pero es el patrón que alguien va a volver a
   usar cuando `/cuadre` agregue filtros por rango de fecha).
3. **Pasar `conn` explícito hacia arriba en `ingestion.py` (ex
   `process.py`)** — **bug real**: hoy `find_duplicate_plate_numbers`
   (process.py:60), `process_rows` (process.py:85) y `replace_order`
   (process.py:193) abren cada una su propia `db.get_conn()`, así que un
   solo `POST /ingest` corre en **4 transacciones separadas** (las 3
   anteriores + el `_options()` del render) — el chequeo de duplicados y el
   insert que protege NO son atómicos entre sí. Con 5 usuarios es una
   carrera tolerable hoy, pero es la forma equivocada para un sistema que va
   a manejar dinero (`unit_price`, `/cuadre`), y el pool de conexiones lo
   hace más barato de seguir haciendo mal, no mejor.
   Arreglo: `process_rows(conn, rows)` / `replace_order(conn, ...)` /
   `find_duplicate_plate_numbers(conn, rows)` reciben la conexión; el
   llamador abre `with db.get_conn() as conn:` y es dueño del commit —
   mismo patrón que ya usa cada función de `db.py`.
   ⚠️ **Los `conn.commit()` internos de `process.py` (líneas 141 y 235)
   tienen que BORRARSE, no "moverse"**: `with db.get_conn() as conn:` de
   psycopg3 ya hace commit solo al salir limpio del bloque; si el `commit()`
   interno sobrevive, `ingestion.py` comitea a mitad de la ruta y el resto
   del trabajo queda en una segunda transacción — se reproduce el mismo bug,
   más sutil. Agregar un test explícito (forzar una excepción después del
   insert y confirmar que TODO se revierte, no solo una parte).
   Se hace en el **mismo commit que el pool** (ambos cambian semántica de
   conexión/transacción, conviene revisar/revertir juntos).
4. **Un dataclass tipado (`class_row`) para la fila "resumen de orden"**
   compartida por las 3 queries del punto 2 y consumida por
   `_orders_table.html` (incluida dos veces desde `records.html`) e
   `ingest.html` — la
   única forma compartida que realmente amerita esto (no hacerlo para cada
   query, solo donde la forma se repite de verdad). Con esto mypy agarra un
   `row.order_numbre` (typo) que hoy pasaría en silencio.
5. **Deduplicar `ingestion.py`** — el problema real de trazabilidad detrás
   de esta discusión: la condición
   `is_real_plate(r.plate_number) and not r.is_voided` está copiada **8
   veces** dentro de `process.py` hoy (líneas 55, 58, 80, 98, 125, 188, 204,
   219), y el bloque de contadores por fila
   (`plates_inserted`/`possible_duplicates`/`voided`/`plate_annulled`/
   `batch_replacements`) está duplicado completo entre `process_rows` y
   `replace_order`. Extraer un helper `_dedup_candidates(rows)` y un helper
   `_count_row(summary, row, is_dup)`, usados por ambas funciones.
   **Además hay una novena copia divergente en `app/web/app.py:622-625`**
   (dentro de `record_save`) que chequea `db.existing_plate_numbers(conn,
   [...])` directo, sin pasar por `ingestion.py` — viola la regla propia del
   proyecto ("ninguna consulta SQL ni regla de negocio debería vivir fuera
   de `app/core/`", `docs/CORE.md:7`) y puede desincronizarse de la lógica
   real. Redirigir esa ruta al helper de `ingestion.py`.
6. **Un docstring de módulo en `db/__init__.py`** dejando escrito el
   contrato que ya se sigue implícitamente: "cada función acá recibe una
   conexión ya abierta y nunca hace commit — quien llama es dueño de la
   transacción." Una frase, documenta la mayor parte del valor que daría una
   interfaz formal, sin ninguna capa nueva. Reflejar lo mismo en
   `docs/CORE.md`.

### Estructura final

```
app/
  core/
    models.py                 # sin cambios
    normalize.py               # sin cambios
    pdf_extract.py              # sin cambios
    ingestion.py                # renombrado de process.py (process_rows, replace_order,
                                 # find_duplicate_plate_numbers, is_alistado -- se le quita el "_" al renombrar,
                                 # ya que app/migrate.py lo importa cruzando el límite del módulo)
    db/                         # paquete, reemplaza el db.py plano
      __init__.py               # get_conn() (con pool, ver abajo) + re-exports explícitos de cada submódulo
      orders.py                 # insert_order, update_order, delete_order, order_detail, recent_orders,
                                 # find_orders_by_number, search_orders_by_number, distinct_requested_by
      plates.py                 # insert_plate, update_plate, update_plate_full, delete_plate, resolve_plate,
                                 # review_rows, existing_plate_numbers, find_orders_for_plate,
                                 # search_plates_by_number, distinct_cities (NO va en inventory.py -- consulta
                                 # plates, no inventory_categories/blank_preparations; incluido acá por
                                 # simetría con distinct_requested_by en orders.py)
      inventory.py               # insert_blank_preparation, recalc_inventory, inventory_view,
                                 # ornamental_count, _consumed_by_material (privada) -- estas dos últimas
                                 # consultan plates pero son detalle de implementación de esta página/función,
                                 # se quedan acá a propósito (excepción deliberada a "agrupar por tabla")
      # customers.py/users.py/pricing.py NO se crean todavía -- son de la Fase 2, crear stubs vacíos
      # ahora sería la sobreingeniería que se pidió evitar
  web/
    app.py                      # se queda con: creación de `app`, context processor, registro de blueprints,
                                 # y las 2 rutas huérfanas (/, /healthz) que no ameritan su propio blueprint
    auth.py                     # sin restructurar (148 líneas, cohesivo) -- gana `landing_url()` (pública,
                                 # antes `_landing_url` privada en app.py:280, se muda acá porque ya opera sobre
                                 # session["role"]/FULL_ACCESS_ROLES que ya son de este módulo)
    helpers.py                  # NUEVO: today_iso(), _options(), _padded(), _validate_order_header(),
                                 # FORM_PLATE_TYPES, INVENTORY_CATS -- lo que usan 2+ blueprints a la vez
    blueprints/
      ingest.py                 # /ingest, /ingest/pdf + _rows_from_form, _sticky_lines_from_form,
                                 # _form_from_pedido (OJO: tests/web/test_ingest_pdf.py la importa directo,
                                 # hay que actualizar ese import en el mismo commit)
      records.py                # /records, /records/<id>, /records/<id>/save, /records/delete,
                                 # /records/search-pdf + _edit_rows_from_form, _order_voided, _record_detail_error
      inventory.py               # /inventory, /inventory/alistado
      review.py                  # /review/*
      auth_routes.py             # /login (GET y POST), /logout
      # pricing.py/cash_summary.py NO se crean todavía -- son de la Fase 5a/5b
    templates/                  # sin cambios
```

**No factory de Flask** (`create_app()`) — una sola config, un solo deploy,
sin necesidad de multi-instancia. Cambiar a factory obligaría a tocar
`web.Dockerfile` (el comando de gunicorn) y `tests/web/conftest.py` sin
resolver ningún problema real. `app = Flask(__name__)` se queda a nivel de
módulo. Los context processors (`_inject_full_access_roles`, app.py:68)
siguen funcionando igual con blueprints — son a nivel de app, no de
blueprint.

**Decisión de import**: `db/__init__.py` re-exporta explícitamente cada
función pública de cada submódulo (no `import *`), así que
`db.insert_order(conn, ...)` **sigue funcionando exactamente igual** en los
9 archivos que importan `db` hoy (incluidas las ~189 llamadas `db.<func>(`
de `tests/core/test_db.py`). Esto contradice la intuición de "que
`__init__.py` solo tenga `get_conn`", pero evita un rename mecánico gigante
sin beneficio real — el problema de trazabilidad era "dónde está definida
cada función", no "cómo se llama". Revisar esta decisión si el paquete `db`
vuelve a crecer mucho después de la Fase 2.

### Pool de conexiones (confirmado: "sí o sí va")

Hoy `db.get_conn()` hace `psycopg.connect(...)` nuevo en cada llamada — sin
pool. Se agrega `psycopg_pool.ConnectionPool` (extra oficial de psycopg3, no
viene incluido con `psycopg[binary]` — cambiar `requirements.txt` a
`psycopg[binary,pool]==3.2.3`).

- **Construcción perezosa, no en el import**: el pool se arma en la primera
  llamada real a `get_conn()`, nunca al importar `app.core.db`. Es
  necesario, no opcional: `tests/conftest.py` importa `app.core.db` siempre,
  incluso cuando `POSTGRES_PASSWORD` no está seteada (para poder correr los
  tests puros de `app/core` sin depender de la BD) — construir el pool en el
  import rompería eso.
- **`get_conn()` pasa a ser un generador con `@contextmanager`** que
  envuelve `pool.connection()`. Los **21** call sites de `app/` que ya usan
  `with db.get_conn() as conn:` (17 en `app.py`, 3 en `process.py`, 1 en
  `migrate.py`) y los 24 de `tests/web/test_app.py` **no cambian ni una
  letra** — `with` funciona igual sobre el generador.
- **Los 6 call sites que hacen `conn = db.get_conn(); ...; conn.close()`
  manual SÍ hay que reescribirlos** a `with db.get_conn() as conn:` —
  llamar `.close()` sobre el generador rompería con `AttributeError`.
  Lista exacta: `tests/conftest.py:170` (`_db_ready`), `:206` (`clean_db`),
  `:223` (`db_conn`); `app/schema_migrate.py:234` (`main()`);
  **`tests/test_schema_migrate.py:308` y `:319`**.
- **Gunicorn**: `web.Dockerfile` corre `gunicorn --workers 2` **sin**
  `--preload` — cada worker importa la app después de hacer fork, así que
  cada worker arma su propio pool en su primer request. No hace falta ningún
  hook `post_fork`. Dejar un comentario explícito en el código: si algún día
  se agrega `--preload`, este razonamiento se rompe y hay que mover la
  construcción del pool a un hook `post_fork` de gunicorn.
- **Tamaño**: `min_size=1, max_size=5` por worker (env-overridable como
  `DB_POOL_MIN_SIZE`/`DB_POOL_MAX_SIZE`, mismo patrón que `DB_HOST`/
  `DB_PORT`) — 2 workers × 5 = 10 conexiones máx. desde la app, contra el
  `max_connections=100` default de Postgres, con margen de sobra para
  `migrate.py`/`schema_migrate.py`/`psql` manual.
- **Cierre**: no hace falta `pool.close()`/hook de `atexit` — gunicorn ya
  espera que terminen los requests en vuelo antes de matar un worker
  (SIGTERM + `--timeout 120`), y esta app no tiene ninguna otra máquina de
  shutdown-graceful.
- **Tests**: `tests/core/test_db.py::TestGetConn` (4 tests, líneas 25-70)
  hoy mockea `psycopg.connect` asumiendo una llamada por invocación — deja
  de ser cierto con un pool (se arma una vez, no por llamada). Reescribir
  esos 4 tests contra una función pura nueva `_conninfo_kwargs()` (separa la
  lectura de env vars de la construcción del pool, sin necesidad de mockear
  nada). El resto de la suite se beneficia gratis: reusar conexiones entre
  los cientos de tests que usan `db_conn`/`clean_db` es más rápido que abrir
  un socket nuevo por test, y como `pool.connection()` hace rollback
  automático al salir del `with` si hubo excepción, el aislamiento no
  cambia.
- **Commit separado del que separa `db.py` en paquete** (aunque tocan el
  mismo archivo, son riesgos distintos: uno mueve código sin cambiar
  comportamiento, el otro sí lo cambia en runtime).

### Secuencia de commits (incremental, no un solo PR atómico)

1. **`row_factory=dict_row`** en `get_conn()` — borra los 8 `dict(zip(...))`
   y los 8 `assert cur.description` de `db.py`. Tests en verde, commit.
   (Antes de tocar la estructura de archivos, para dividir código ya limpio.)
2. **`psycopg.sql.SQL`** para deduplicar la query de resumen de orden (3
   copias casi idénticas) y matar el f-string de `_consumed_by_material`
   (`db.py:149`). Tests en verde, commit.
3. **Dataclass tipado (`class_row`)** para la fila de resumen de orden usada
   por esas 3 queries + `_orders_table.html`/`ingest.html`. Tests en verde,
   commit.
4. **Renames de nomenclatura** (`CLAUDE.md`), sin cambio de comportamiento:
   - `app/core/process.py` → `app/core/ingestion.py`; `_is_alistado` →
     `is_alistado` (pública). Actualizar imports en `app/web/app.py:42`,
     `app/migrate.py:26` y `tests/core/test_process.py:19` (renombrar
     también ese archivo a `tests/core/test_ingestion.py`).
   - `app/schema_migrate.py`: `backfill_clientes` → `backfill_customers`,
     `backfill_usuarios` → `backfill_users`, `BACKFILL_CLIENTES_VERSION` →
     `BACKFILL_CUSTOMERS_VERSION`, `BACKFILL_USUARIOS_VERSION` →
     `BACKFILL_USERS_VERSION`. ⚠️ **Los VALORES string
     (`"0002_backfill_clientes"` / `"0003_backfill_usuarios"`) NO se tocan**
     — ya están escritos en `schema_migrations` de producción y cambiarlos
     re-dispararía el backfill contra datos reales (está explicado en el
     docstring de `app/schema_migrate.py`; actualizar ese docstring para que
     siga describiendo la realidad). Actualizar también los nombres de test
     en `tests/test_schema_migrate.py` (`..._cliente`/`..._clientes`/
     `..._usuario`/`..._usuarios`).
   Tests en verde, commit.
5. **Deduplicar `ingestion.py`** (ver mejora 5): extraer
   `_dedup_candidates`/`_count_row`, usarlos en `process_rows` y
   `replace_order`, y redirigir el chequeo divergente de
   `app/web/app.py:622-625` para que pase por acá. Tests en verde, commit.
6. **Separar `db.py` → paquete `db/`** (con la ubicación de
   `distinct_cities` de arriba). Cero call sites fuera de `db/` cambian (por
   el re-export explícito). Agregar el docstring de contrato de transacción
   (mejora 6) a `db/__init__.py` y a `docs/CORE.md`. Tests en verde, commit.
7. **Commit combinado — pool de conexiones + `conn` explícito en
   `ingestion.py`** (deliberadamente juntos: ambos cambian semántica de
   conexión/transacción):
   - Agregar `psycopg[binary,pool]==3.2.3`, pool perezoso en
     `db/__init__.py`, función pura `_conninfo_kwargs()`.
   - `process_rows(conn, rows)` / `replace_order(conn, ...)` /
     `find_duplicate_plate_numbers(conn, rows)` reciben la conexión,
     **borrando** sus `conn.commit()` internos (process.py:141 y :235).
   - Actualizar **todos** los llamadores, no solo los de `app.py`:
     · `app/web/app.py:364` (`replace_order`), `:383`
       (`find_duplicate_plate_numbers`), `:396` (`process_rows`) — la ruta
       `ingest_post` pasa a abrir **un solo** `with db.get_conn() as conn:`
       que envuelve los tres y hace el commit.
     · **`app/migrate.py:234` y `:250` (`process_rows`)** — este script
       corre contra producción, no dejarlo fuera del refactor. Las dos
       llamadas están en ramas distintas del mismo `if args.since: ... else:`:
       la de `:234` (rama `--since`) necesita un `with db.get_conn() as conn:`
       **nuevo**; la de `:250` (rama reconciliada) cae justo después del
       `with` que ya existe en `:243` para insertar
       `RECONCILED_ALISTAMIENTO` — ampliar ese bloque para que también
       envuelva el `process_rows(plate_rows)` y borrar su `conn.commit()`
       de `:249`, de modo que el alistamiento reconciliado y las placas
       queden en UNA sola transacción.
     · `tests/core/test_ingestion.py` — ~35 llamadas a
       `process_rows`/`replace_order`/`find_duplicate_plate_numbers`; todas
       pasan a recibir el `db_conn` del fixture.
   - Reescribir `tests/core/test_db.py::TestGetConn` (4 tests) contra
     `_conninfo_kwargs()`.
   - Reescribir los 6 call sites manuales listados arriba a `with`.
   - Agregar el test explícito de atomicidad (excepción a mitad de ruta →
     todo se revierte).
   - **Corregir los docstrings/comentarios que afirman lo contrario** (hoy
     dicen "cada `get_conn()` abre una conexión nueva, sin punto de
     inyección"): `app/core/db.py` (docstring de módulo, al agregar el pool),
     `tests/conftest.py:190` (docstring de `clean_db`),
     `tests/core/test_ingestion.py:4-8` (ex `test_process.py`, docstring de
     módulo), `tests/core/test_db.py:3-8` (docstring de módulo),
     `tests/web/test_app.py:219` (comentario de sección) y
     `docs/LOCAL_DEVELOPMENT.md:43-46`.
   Tests en verde, commit.
8. **Extraer `app/web/helpers.py`** y mover `_landing_url` (app.py:280) a
   `auth.py` como `landing_url()` — las rutas siguen en `app.py` en este
   paso, solo se mueven los helpers. Tests en verde, commit.
9. **Crear `app/web/blueprints/`, uno a la vez, un commit por blueprint**:
   `review.py` (el más simple) → `inventory.py` → `auth_routes.py` (`/` y
   `/healthz` se quedan directo en `app.py`) → `records.py` → `ingest.py`
   (actualizar el import de `tests/web/test_ingest_pdf.py` y los
   `monkeypatch.setattr("app.web.app.process_rows", ...)` de
   `tests/web/test_ingest_pdf.py:112` y
   `tests/web/test_records_search_pdf.py:65`, que apuntan al módulo viejo).
   Tests en verde después de cada uno.
10. **Fast-follow recomendado, no bloqueante -- reorg de tests HECHA
    (2026-09-21)**: `tests/web/test_app.py` (llegó a 2096 líneas) y
    `tests/core/test_db.py` (llegó a 1529 líneas) ya eran más grandes que
    el código que prueban, y sin dividirlos se recreaba el mismo problema
    de trazabilidad del lado de los tests. Se dividió `test_app.py` (ya
    organizado por clase `TestXRoute`) en `tests/web/test_login.py`/
    `test_review.py`/`test_inventory.py`/`test_records.py`/`test_ingest.py`/
    `test_roles.py` (más `tests/web/_helpers.py` para los helpers
    compartidos `make_row`/`_insert_order`), y `test_db.py` en el paquete
    nuevo `tests/core/db/` (`test_connection.py`/`test_orders.py`/
    `test_plates.py`/`test_inventory.py`/`test_customers.py`/
    `test_users.py`/`test_pricing.py`, más `tests/core/db/_helpers.py`) —
    mismo espejo que el código fuente, cero cambio de comportamiento (789
    tests, antes y después). `test_roles.py` quedó aparte porque
    `TestProduccionRole`/`TestPuntoVentaRole` cruzan varios blueprints a la
    vez, no encajan en un solo archivo por blueprint.

    **También HECHO (2026-09-21)**: convertir `clean_db` de `TRUNCATE` a
    una transacción con rollback por test (lo habilita el paso 7: ahora que
    `ingestion.py` recibe `conn` en vez de abrir el suyo, un test puede
    envolver todo en una sola transacción y revertirla al final) — real,
    no trivial: `conn.commit()`/`.rollback()` se llaman explícitamente en
    código real (`records.py`/`review.py`/`pricing.py`/`inventory.py`) y en
    ~100 sitios de test, así que `psycopg`'s `conn.transaction()` (que
    prohíbe llamarlos dentro de su propio context manager) no sirve — la
    solución fue `SAVEPOINT`/`RELEASE SAVEPOINT`/`ROLLBACK TO SAVEPOINT` a
    mano, reasignando `conn.commit`/`conn.rollback` para operar sobre ese
    savepoint en vez de la base real, con cuidado especial de replicar el
    "commit sobre una transacción abortada = rollback implícito, sin error"
    que Postgres da gratis y que varios tests dependían de eso sin saberlo
    (`tests/test_schema_migrate.py`, `TestCreateUser::
    test_duplicate_username_raises`). Verificado con la suite completa
    (789 tests) corrida 3 veces seguidas sin cambios de resultado. **No**
    habilita correr `pytest` en paralelo contra el mismo `plates_test`
    físico — se investigó y se descartó explícitamente esa parte de la
    promesa original de este paso (ver el docstring de `clean_db` y
    `docs/LOCAL_DEVELOPMENT.md` para el porqué): el beneficio real logrado
    es velocidad, no paralelismo.
11. **Reescribir la documentación** al final, una vez asentado el código:
    `docs/ARCHITECTURE.md` (el diagrama nombra `app/core/process.py` y
    `app/core/db.py` explícitamente), `docs/CORE.md` (secciones `## process.py`
    y `## db.py`), `docs/WEB.md` y `docs/LOCAL_DEVELOPMENT.md`.

## Fase 1e — Monitoreo y observabilidad (mayormente en paralelo con la 1d)

Hoy no hay nada: dos `app.logger.exception()` sueltos, un `/healthz` que
nadie consulta, cero rotación de logs (verificado: `docker-compose.yml` no
tiene bloque `logging:`, y no hay `sentry` en el repo ni en
`requirements.txt`). Con dinero real entrando al sistema esto pasa a ser
necesario, no un lujo. Alcance deliberadamente mínimo — nada de
Prometheus/Grafana/ELK/APM, es sobreingeniería para un VPS con Docker
Compose y un puñado de usuarios.

⚠️ **Conflicto de archivos con la 1d — ordenar estos dos ítems**: la 1e toca
`requirements.txt`, `docker-compose.yml` y `app/web/app.py` (el init de
Sentry), y la 1d reescribe `app.py` entero y también toca
`requirements.txt` (paso 7). Los dos ítems que NO tocan `app.py` (rotación
de logs y UptimeRobot) se pueden hacer en paralelo con la 1d sin riesgo. El
init de Sentry conviene hacerlo **después del paso 9 de la 1d** (cuando
`app.py` ya quedó reducido a creación de app + registro de blueprints), o
aceptar resolver el conflicto a mano. No arrancar el init de Sentry en un
worktree paralelo mientras la 1d está a mitad de camino.

- **Sentry** (`sentry-sdk[flask]`, plan gratuito alcanza): captura
  automáticamente cualquier excepción no manejada, incluyendo los
  `app.logger.exception()` ya existentes, con alerta por correo y stack
  trace completo. Se inicializa una vez en `app.py`, justo después de
  `app = Flask(__name__)`, con el DSN vía variable de entorno
  (`SENTRY_DSN`; si no está seteada, no inicializar — así dev local y los
  tests no mandan nada).
- **UptimeRobot** (gratis, sin cambios de código): ping a
  `https://placas.ingse.co/healthz` cada 5 minutos, alerta si cae.
  Configuración externa, no toca el repo salvo mencionarlo en
  `docs/ARCHITECTURE.md`.
- **Rotación de logs de Docker**: agregar a `docker-compose.yml` (servicio
  `web`, y `db` si aplica):
  ```yaml
  logging:
    driver: json-file
    options:
      max-size: "10m"
      max-file: "3"
  ```
  Sin esto, los logs de los contenedores crecen sin límite en el disco del
  VPS indefinidamente.

## Fase 2 — `app/core` (models, normalize, ingestion, db) + tarifas

**Requiere que la Fase 1d ya esté mergeada** — se construye contra la
estructura de paquetes nueva (`app/core/db/orders.py`,
`app/core/ingestion.py`), no contra `db.py`/`process.py` planos. Los módulos
nuevos (`db/customers.py`, `db/users.py`, `db/pricing.py`) siguen el mismo
patrón de paquete que estableció la 1d.

- `models.py`: ~15 campos nuevos opcionales en `ExtractedRow`, todos con
  default `None` (así `app/migrate.py` sigue sin tocarse). Sumarlos al
  validador `_blank_to_none` existente (si no, un string vacío del form
  queda como `""` en vez de `NULL`, y rompe cualquier `IS NULL` en
  `/cuadre`). `delivery_date`/`payment_date` necesitan un validador
  `mode="before"` que mapee `""` → `None` (un `<input type="date">` vacío
  postea `""`, que pydantic rechaza tal cual). **`delivery_date` NO debe
  pasar por el guard de "fecha no futura" de `_validate_order_header`** —
  una fecha de entrega futura es válida y esperada.
- `normalize.py`: vocabularios fijos (nombres de constante en inglés,
  *valores* en español) + un helper genérico
  `normalize_choice(value, allowed) -> str | None`: vacío → `None`; si
  matchea (comparando plegado/sin acentos) devuelve la entrada canónica de
  `allowed`; si no matchea, `None` — nunca inventa un valor, mismo espíritu
  que `is_real_plate`/`pdf_extract` ("fiel, no inteligente"). El PDF muestra
  un valor no reconocido como aviso en `notices` para que la persona lo
  elija del dropdown.

  ```python
  RECEIPT_TYPES       = ("FACTURA", "RECIBO", "OTROS")
  CUSTOMER_TYPES      = ("PERSONA NATURAL", "TRÁNSITO", "EMPRESA", "OTROS")
  DOCUMENT_TYPES      = ("NIT", "CÉDULA", "OTROS")
  DOCUMENT_RELATIONS  = ("DUEÑO", "POSEEDOR", "OTROS")   # el borrador tenía "POSEDOR", es typo
  PAYMENT_METHODS     = ("EFECTIVO", "TRANSFERENCIA", "CRÉDITO", "OTROS")
  REASONS             = ("DETERIORO", "PÉRDIDA", "OTROS")
  ```
- `core/ingestion.py`: `process_rows`/`replace_order` resuelven/crean el
  cliente a partir de la misma fila `head`/`group[0]` que ya usan para
  `order_date`/`requested_by`; sacan el precio vigente (`current_prices`)
  antes de cada `insert_plate`; aceptan `created_by: int | None` →
  `production_orders.created_by`. **Generalizar** el chequeo que hoy solo
  existe para `requested_by` ("¿todas las filas del grupo coinciden?") a
  **todos** los campos a nivel de orden (`receipt_type`/`receipt_number`,
  `payment_method`, `tracking_number`, `client_order_number`,
  `delivery_date`, `payment_date`, `dispatched_by`, `received_by`, `notes`,
  cliente resuelto) — iterar una tupla `_ORDER_LEVEL_FIELDS` y emitir un
  `review_notes` por cada campo que no coincida dentro del mismo grupo.
  `reason`/`vehicle_brand`/`unit_price` quedan afuera (son genuinamente por
  línea).
  **`replace_order` vuelve a tomar la tarifa vigente AL MOMENTO DEL
  REEMPLAZO**, no la que regía en la fecha original de la orden —
  documentarlo en el docstring de `replace_order` y en el texto del
  `confirm()` de "Reemplazar" en `ingest.html` ("...y los precios se toman
  de la tarifa vigente hoy, no la de la fecha original").
- `core/db/customers.py` (nuevo): CRUD de `customers`
  (`resolve_or_create_customer(conn, name) -> int`, reusado por
  `ingestion.py` Y por `record_save` — ver Fase 3).
- `core/db/users.py` (nuevo): lookup/CRUD de `users`, usado por `auth.py`.
- `core/db/pricing.py` (nuevo): `current_prices`/`price_list`/`set_price`.
- `core/db/orders.py`: hacer crecer `insert_order` (pasar la fila
  `head`/`ExtractedRow` completa + `customer_id` en vez de ~13 kwargs
  sueltos, igual que ya hace `insert_plate(conn, *, order_id, row,
  is_possible_duplicate)`), `order_detail` (+ `JOIN customers` para traer
  sus atributos), `update_order` (+ `customer_id`). Las queries de listado
  (`recent_orders`, `find_orders_by_number`, `search_orders_by_number`)
  **no cambian de forma** — siguen usando `requested_by` tal cual, así que
  `_orders_table.html` no necesita tocarse.
- `core/db/plates.py`: `insert_plate` (+ `unit_price`), `update_plate_full`
  y `update_plate` (+ regla de re-snapshot de `unit_price`;
  `search_plates_by_number` no cambia de forma).
- `auth.py`: pasa de leer `AUTH_USERS`/`AUTH_ROLES` de variables de entorno
  a consultar `users` (vía `core/db/users.py`, nunca SQL inline en
  `auth.py`). El login guarda `session["user_id"]` además de `user`/`role`.
  Respetar los literales de rol sin tilde (ver nota en la tabla `users`).
- **Límite con la Fase 5a**: todo lo de precios en `core/db/pricing.py` se
  hace ACÁ — la Fase 5a es *solo* la ruta `/precios` + template, para que no
  se pisen si corren en paralelo.
- **`recalc_inventory` no se toca** — ver la nota de separación
  precio/inventario arriba.
- **Bug de paso, encontrado por el agente de la Fase 1b**: la rama
  ALISTAMIENTO de `ingestion.py` usa `row.plate_type` directo como
  `category` en vez de mapearlo con `normalize.inventory_category()`. Es
  código muerto hoy (el flujo real de `/inventory/alistado` no pasa por
  ahí), pero corregirlo de una vez ya que se reescribe esta parte.

## Fase 3 — UI de `/ingest`

- Fieldset de orden: comprobante (`receipt_type`/`receipt_number`), cliente
  (con autocompletado que reutiliza datos si ya existe), forma de pago
  (`payment_method`, `<select>` obligatorio), guía#/pedido#
  (`tracking_number`/`client_order_number`), fechas
  (`delivery_date`/`payment_date`), responsable/quien recibe
  (`dispatched_by`/`received_by`), observaciones (`notes`).
- Tabla de líneas de placa: nuevas columnas `reason`/`vehicle_brand`/
  **`unit_price`** por fila. `unit_price` se pre-llena vía JS con la tarifa
  de `plate_type_prices` al elegir el tipo de placa de esa línea (mismo
  patrón de `HINTS`/placeholders por tipo que ya usa `ingest.html`), pero es
  un input editable normal. `transit_license` va en el bloque de cliente del
  header (solo visible si `type` = PERSONA NATURAL).
- Ruta `@pos_allowed` (`admin` + `punto_venta`), reemplaza el
  `@login_required` actual en `/ingest`, `/ingest/pdf` y
  `/records/<id>/save`.
- **El form hoy arma el diccionario "sticky" (para repoblar el form si
  aparece un aviso de duplicado) en 3 lugares distintos e independientes**
  (dos veces inline en `ingest_post`, `app.py:375-379` y `:389-393`; una vez
  en `_form_from_pedido`, `app.py:261`) — con ~15 campos nuevos, cualquiera de los tres
  que quede desactualizado hace que la persona tenga que retipear todo el
  bloque de cliente/pago cada vez que rebota un aviso. Factorizar en un solo
  helper `_sticky_header_from_form(form) -> dict` usado en ambos rebotes, y
  hacer que `_form_from_pedido` devuelva exactamente el mismo set de claves.
  `_sticky_lines_from_form` también necesita sumar `reason`/`vehicle_brand`
  envueltos en `_padded(...)` como ya se hace con `city` (si no, una lista
  opcional más corta trunca el `zip` y desaparecen líneas de placa
  silenciosamente).
- Paridad en `record_detail.html`/`record_save`: además de los campos
  nuevos, `record_save` debe llamar a `db.resolve_or_create_customer` si se
  edita el nombre del cliente (hoy no re-resuelve `customer_id` en absoluto)
  — ver B5 para el alcance exacto de qué se puede editar.

## Fase 4 — Automatización por PDF (depende de la forma del `header` dict de la Fase 3)

**El PDF tiene campos tanto ARRIBA como DEBAJO de la tabla de placas** —
confirmado corriendo `pdfplumber` sobre los dos PEDIDO reales. Hoy
`_find_header_value` solo busca en `header_rows = rows[:header_index]` (todo
lo de ARRIBA de la tabla). Seis de las etiquetas nuevas (`RESPONSABLE
DESPACHO...`, `TELÉFONO:`, `NOMBRE QUIEN RECIBE...`, `FORMA DE PAGO:`,
`OBSERVACIONES:`, y `CIUDAD Y DIRECCIÓN DE ENTREGA:` que queda fuera de
alcance) están DEBAJO de `TOTAL PEDIDO:`, en el pie de página — son
físicamente invisibles para el código tal cual está hoy.

- Agregar un segundo bloque `footer_rows = rows[footer_start:]` (desde la
  fila que empieza con `RESPONSABLE DESPACHO`) además del `header_rows`
  existente, y buscar ahí las etiquetas del pie. **No tocar**
  `_find_header_value` en sí (el guard `_LABEL_PEDIDO_HASH_BARE` vive
  adentro y debe seguir aplicando gratis a cualquier lookup nuevo).
- **Las etiquetas deben ser el texto impreso EXACTO** (el matching es por
  igualdad, no por substring) — el nombre real es `RESPONSABLE DESPACHO
  (INGSE SAS):` y `NOMBRE QUIEN RECIBE EL PEDIDO:`, no las versiones cortas.
- Mapea (header): `CLIENTE:` (resuelve/crea cliente igual que en manual —
  `NIT / CÉDULA:` → `document_number`, puede venir en blanco si la celda de
  valor cae en una fila distinta a la de la etiqueta por un artefacto de
  celdas combinadas; mantener el escaneo "misma fila solamente" tal cual,
  NUNCA agregar un heurístico de "mirar la fila siguiente" — eso arriesga
  traer el valor de otro campo), `GUÍA #:` → `tracking_number`, `PEDIDO #:`
  → `client_order_number` (aparece solo en la tabla declarativa nueva, nunca
  en `_LABELS_ORDEN`), `FECHA ENTREGA:` → `delivery_date`,
  `FECHA TRANSF./PAGO:` → `payment_date`.
  Mapea (footer): `RESPONSABLE DESPACHO (INGSE SAS):` → `dispatched_by`,
  `TELÉFONO:` → `customers.phone`, `NOMBRE QUIEN RECIBE EL PEDIDO:` →
  `received_by`, `FORMA DE PAGO:` → `payment_method`, `OBSERVACIONES:` →
  `notes`.
- **Los campos de dinero del PDF se ignoran a propósito**, igual que
  `TRÁNSITO:`: el `VALOR:` del header, `VALOR PLACA`/`VALOR TOTAL` de las
  líneas, y `TOTAL PEDIDO:` del pie — `unit_price` siempre sale de la tarifa
  (`plate_type_prices`) como sugerencia pre-llenada y editable, nunca se lee
  del documento. (`TOTAL PEDIDO:` sigue cumpliendo su rol actual de marcar
  el fin de la tabla de placas, y ahora también el inicio del bloque de pie.)
- **Siempre manuales, sin campo impreso en ningún lado del PDF**:
  `receipt_type`, `receipt_number`, `customers.type`,
  `customers.document_type`, `customers.document_relation`,
  `customers.email`, `customers.transit_license`, y por línea
  `reason`/`vehicle_brand`.
- Nuevos fixtures sintéticos en `tests/pdf_fixtures.py` con filas de pie de
  página con valores reales (hoy el fixture ya agrega esas filas de etiqueta
  pero sin valores) + un caso con el offset de `NIT / CÉDULA` para confirmar
  que devuelve `""` y no un valor incorrecto.

## Fase 5a — Tarifas (`/precios`)

SOLO `blueprints/pricing.py` + template (la lógica de `core/db/pricing.py`
ya se hizo en la Fase 2). No toca `ingest.html`/`ingestion.py`. Ruta
`@login_required` (admin-only).

## Fase 5b — Cuadre de caja (`/cuadre`)

Depende de que la Fase 3 ya esté generando `payment_method` real y de la
Fase 5a para las tarifas. La lógica de agregación (la query de la sección
"Cuadre de caja") va en `core/cash_summary.py` (nuevo — hay lógica real más
allá de CRUD, amerita su propio módulo de orquestación en vez de vivir
directo en un `db/*.py`); la ruta va en `blueprints/cash_summary.py`. Ruta
`@pos_allowed`. Incluye la vista HTML y la ruta/botón de exportar el mismo
reporte a PDF simple.

## Fase 6 — Limpieza de UI (ya hecha para las plantillas existentes)

La pasada sobre las plantillas existentes **ya se hizo** (PR #13): grep
confirma que hoy no queda ningún emoji/símbolo decorativo en
`app/web/templates/`. Lo que queda es **aplicar el mismo criterio a las
plantillas nuevas de este feature**:

- Sin emojis ni símbolos decorativos en el HTML — texto plano o CSS (ej. un
  botón de quitar línea dice "Eliminar", no "✕").
- Sobre el texto: **dejar solo lo que explica cómo usar algo** (ej.
  "Disponible = Alistado − Consumido..." en `inventory.html`, o "Subí el PDF
  del pedido para revisar..." en `records.html`) y **cortar lo puramente
  decorativo/redundante**. Revisar cada `<p class="muted">`/similar de las
  plantillas nuevas con ese criterio.

## Fase 7 — Documentación (después de la 5b)

- `docs/DATABASE.md` — revisar por si algo cambió respecto de lo ya
  documentado en las Fases 1/1b/1c.
- `docs/CORE.md` — funciones nuevas de `db`/`normalize`/`ingestion`, la
  regla de re-snapshot de `unit_price`, el comportamiento de re-precio de
  `replace_order`.
- `docs/WEB.md` — `/precios` y `/cuadre` en la tabla de rutas, con sus
  decoradores; el rol `punto_venta`.
- `docs/ARCHITECTURE.md` — la tabla de CI/CD hoy describe `deploy.yml` sin
  mencionar el paso `python -m app.schema_migrate` que ya corre; agregarlo
  junto con el runner de migraciones como pieza de infraestructura.
- `README.md` — cómo correr las migraciones localmente.

## Orden de ejecución

```
Fase 1   (schema + migración + hook de deploy)   ── HECHA (PR #14)
Fase 1b  (FKs reales)                            ── HECHA (PR #16)
Fase 1c  (rename a inglés)                       ── HECHA (PR #17)
Fase 6   (limpieza UI de plantillas existentes)  ── HECHA (PR #13)
        │
        ▼
Fase 1d (refactor: db/ paquete, blueprints, pool)  ── bloquea la Fase 2
        │
        ▼
Fase 2 (core: models, normalize, db —incl. tarifas—, ingestion)
        │
        ├──► Fase 5a (solo ruta/template /precios)  ── worktree en paralelo
        │
        ▼
Fase 3 (UI /ingest + paridad record_detail/record_save)
        │
        ├──► Fase 4 (PDF: header + pie de página)
        └──► Fase 5b (/cuadre)
                │
                ▼
        Fase 7 (docs)

Fase 1e (monitoreo) ── logs+UptimeRobot en paralelo con la 1d; el init de
                       Sentry después del paso 9 de la 1d (conflicto en app.py)
```

**Importante sobre "worktree en paralelo"**: paralelizar la ESCRITURA de
código en distintos worktrees está bien, pero **correr la suite de tests de
dos worktrees al mismo tiempo NO es seguro** — `docker-compose.yml` usa
nombres/puertos fijos (`plates-db-1`:5433, `plates-web`:8000), la BD de test
es una sola `plates_test` compartida, y `clean_db` hace `TRUNCATE` sin
aislar por transacción (`docs/LOCAL_DEVELOPMENT.md`). Dos worktrees
corriendo `pytest` a la vez se van a pisar las tablas entre sí. Solución:
seguir usando el lock de archivo (`/tmp/plates_docker_lock`) — solo un
worktree corre tests de BD a la vez. (El paso 10 de la Fase 1d levanta esta
restricción al pasar `clean_db` a rollback transaccional.)

Todo lo que queda por hacer es código nuevo aditivo o refactor sin cambio de
schema; las únicas fases que tocaron filas reales de producción ya se
ejecutaron. Si alguna fase futura vuelve a necesitar una migración, aplica
la misma regla: `pg_dump` de respaldo y prueba contra un dump restaurado en
local antes de tocar la base real.

## Instrucción para cualquier agente que ejecute esto

**No asumir ninguna decisión de negocio/esquema ambigua — preguntar
directamente al usuario y esperar respuesta, tal como se hizo en esta misma
conversación campo por campo.** Esto aplica en particular a cualquier campo
nuevo que aparezca al implementar y no esté explícitamente resuelto en este
documento, y a las preguntas **B4/B5** que quedaron con un default razonable
pero no confirmado explícitamente — si al implementar la Fase 3 se necesita
más precisión sobre cualquiera de las dos, preguntar antes de asumir el
default.

## Verificación

**Fase 1d (refactor)**
- Suite completa en verde después de CADA commit de la secuencia (no solo al
  final) — si algo rompe, tiene que ser obvio cuál paso lo rompió.
  `ruff`/`mypy` limpios.
- Verificación manual en Docker: cada ruta sigue respondiendo igual que
  antes del refactor (probar al menos una de cada blueprint).
- `docker compose exec web python -m app.schema_migrate` sigue funcionando
  (usa `db.get_conn()`, ahora paquete) y sigue siendo un no-op en la segunda
  corrida — confirmar que `schema_migrations` no ganó filas nuevas después
  del rename de constantes del paso 4.
- `python -m app.migrate --help` (o una corrida en seco) sigue funcionando
  tras el cambio de firma de `process_rows`.

**Fase 1d (pool)**
- 2 requests concurrentes a rutas distintas no se bloquean entre sí ni
  agotan el pool (`max_size=5`).
- Matar y recrear el contenedor `web` no deja conexiones huérfanas en
  Postgres (`SELECT count(*) FROM pg_stat_activity`).
- Los 6 call sites reescritos a `with db.get_conn()` (3 fixtures de
  `tests/conftest.py`, `schema_migrate.py:main()`, 2 en
  `tests/test_schema_migrate.py`) siguen limpiando conexiones entre tests.
- Test de atomicidad: una excepción a mitad de `POST /ingest` revierte
  TODO (ni la orden ni las placas ni el `recalc_inventory` quedan escritos).

**Fase 1e**
- Sentry captura un error de prueba forzado y aparece en el dashboard.
- UptimeRobot configurado y confirmando "up" contra `/healthz`.
- `docker compose logs web` respeta el `max-size`/`max-file` configurado
  (verificar con `docker inspect` el `LogPath` y su tamaño después de
  generar tráfico).

**Fase 2 en adelante**
- `replace_order` después de un cambio de tarifa → las placas nuevas quedan
  con la tarifa vigente al momento del reemplazo, no la original
  (comportamiento documentado, no un bug).
- Editar `plate_type` en una placa guardada re-toma `unit_price`; editar
  solo `quantity` no lo toca.
- `record_save` renombrando el cliente mueve `customer_id` y mantiene
  `requested_by` sincronizado con `customers.name`.
- Rebote de aviso de duplicado en `/ingest` conserva todos los campos POS
  nuevos y los de línea (`reason`/`vehicle_brand`).
- `/cuadre`: aparece el bucket "SIN FORMA DE PAGO"; se excluyen
  `is_voided`/`is_plate_annulled`; una placa con `unit_price NULL` genera
  aviso y NO cuenta como 0.
- `pdf_extract`: fixture con filas de pie de página parsea los campos del
  footer; el guard de `PEDIDO #` bare sigue funcionando; `PEDIDO #: 29` cae
  en `client_order_number` y nunca en `order_number`; `NIT / CÉDULA` con
  valor desalineado de fila devuelve `""`.
- `/precios` devuelve 403 para todo lo que no sea `admin` (incluido
  `punto_venta`). `/cuadre` devuelve 403 para `consulta`/`produccion` pero
  200 para `punto_venta`. `punto_venta` puede entrar a `/ingest` y guardar
  una orden, pero recibe 403 en `/records/delete`, `/inventory/alistado` y
  `/review/*`. Los usuarios existentes (`admin`, `produccion`, `consulta`)
  siguen pudiendo loguearse después de mover `auth.py` a la tabla `users`.
- Suite completa en verde + `ruff`/`mypy`.
- Verificación manual en Docker: cargar una orden nueva con cliente nuevo,
  cargar otra con el mismo nombre de cliente y confirmar que autocompleta,
  revisar `/cuadre` de ese día.

---

## Actualización: tipos de cliente y datos de persona

`customers.type` ahora tiene 4 valores (`normalize.CUSTOMER_TYPES`):
`TRÁNSITO`, `EMPRESA`, `GESTOR` y `CLIENTE` (el antiguo `PERSONA NATURAL`
renombrado; `OTROS` desapareció). `DOCUMENT_TYPES` suma `CÉDULA DE EXTRANJERÍA`.
Se agregaron las columnas `address`, `neighborhood` y `municipality` (solo para
`GESTOR` y `CLIENTE`), Propietario/Poseedor (`document_relation`) aplica solo a
`CLIENTE`, y se eliminó `customers.transit_license` (el número de licencia pasa
a ser un dato de la tarjeta de propiedad, no del cliente). Migración
`app/migrations/0005_customer_types_and_person_fields.sql`. Las menciones
anteriores de `PERSONA NATURAL`, `OTROS` como tipo de cliente y
`transit_license` en este documento describen el diseño original.

## Nota: capa de datos de tarjetas de propiedad (Fase 2 del plan de declaraciones)

Existe la tabla `ownership_cards` (migración `0006`) y su capa de datos
`app/core/db/ownership_cards.py`, incluido `find_card_conflicts`. Todavía no
hay UI ni flujo de declaraciones que la use; ver `docs/DATABASE.md` y
`docs/CORE.md`.

## Nota: declaraciones, lógica de negocio (Fase 3 del plan de declaraciones)

Existe la tabla `declarations` (migración `0007`), su capa de datos
`app/core/db/declarations.py` y la lógica `app/core/declarations.py`
(validar, enviar, aprobar, rechazar), más `db.last_fabrication_date` para la
regla de 6 meses. Todavía no hay formulario público ni pantalla interna que la
usen; ver `docs/DATABASE.md` y `docs/CORE.md`.

## Nota: enlace formal placa fabricada -> tarjeta (última fase del plan de declaraciones)

La migración `0008` agrega `plates.ownership_card_id` (nullable, FK a
`ownership_cards`). El enlace es automático y silencioso: al insertar o editar
una placa se busca la tarjeta con el mismo número de placa. Solo se intenta para
órdenes de clientes `CLIENTE`, `GESTOR` o sin tipo; nunca para `EMPRESA` ni
`TRÁNSITO`, ni para rangos, reposiciones, placas anuladas o sin placa real. El
detalle del Registro muestra la licencia de la tarjeta enlazada. Reglas y
columna: `docs/DATABASE.md`; funciones: `docs/CORE.md`.

## Nota: pantalla interna `/declaraciones`

Existe la pantalla de revisión `/declaraciones` (blueprint `declarations.py`):
lista con filtro de estado y búsqueda, detalle con la explicación de cada
alerta y su contexto real, descarga autenticada de firmas y PDF, y aprobar o
rechazar (solo admin). Listar, ver y descargar es `@pos_allowed`; el rol
`production` recibe 403 en todo. `/customers/<id>` muestra además las tarjetas
de propiedad del cliente. Ver `docs/WEB.md`.

## Nota: ruta pública `/declaracion` (Fase 4 del plan de declaraciones)

Existe `app/web/blueprints/public_declaration.py`: la primera ruta pública que
escribe (`GET/POST /declaracion`, sin login). Combina honeypot, token firmado,
Turnstile y límite por IP contra `declarations`, guarda firmas y PDF en
`UPLOADS_DIR` y llama a `submit_declaration`. Queda apagada (503) hasta que se
configuren las llaves de Turnstile. `tests/web/test_public_routes.py` fija la
lista de rutas públicas; ver `docs/WEB.md`.
