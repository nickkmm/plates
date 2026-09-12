# Documentación de referencia — plates

Plates es la app de seguimiento de producción de placas vehiculares de
INGSE (Colombia): reemplaza un flujo antes basado en Excel, con tres roles
(`admin` con acceso completo; `producción` y `consulta` de solo lectura)
sobre un registro único de órdenes de producción y las placas que
contienen.

Esta carpeta es documentación **técnica de referencia** — para entender
cómo funciona el sistema por dentro. Cómo desplegarlo, configurar auth o
levantar el entorno local está en el [`README.md`](../README.md) raíz; acá
no se repite.

## Mapa de documentos

| Documento | Qué cubre |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Componentes, despliegue (Docker/redes), pipeline CI/CD |
| [`DATABASE.md`](DATABASE.md) | Esquema completo, diagrama ER, columnas, rarezas de datos |
| [`CORE.md`](CORE.md) | `app/core/`: normalización, extracción de PDF, pipeline de ingesta |
| [`WEB.md`](WEB.md) | `app/web/`: rutas, roles/permisos, templates, flujos completos |
| [`LOCAL_DEVELOPMENT.md`](LOCAL_DEVELOPMENT.md) | Gotchas de infra local, testing, variables de entorno |

## Vista general del sistema

```mermaid
flowchart LR
    subgraph Usuarios["Usuarios (por rol)"]
        A[admin]
        P[producción]
        C[consulta]
    end
    subgraph VPS["VPS — Docker Compose"]
        WEB["plates-web<br/>Flask + gunicorn"]
        DB[("plates-db-1<br/>Postgres 16")]
        WEB -->|psycopg3| DB
    end
    A -- login --> WEB
    P -- login --> WEB
    C -- login --> WEB
    GH["GitHub Actions<br/>(push a main)"] -- "SSH deploy" --> VPS
    WEB -.->|"red edge compartida"| INF["stack 'informes'<br/>(otro proyecto, mismo VPS)"]
```

Ver [`ARCHITECTURE.md`](ARCHITECTURE.md) para el detalle de componentes y
de despliegue, incluido un incidente real de producción que motivó parte
del diseño actual.

> **Nota:** el esquema de base de datos documentado acá va a cambiar
> pronto (hay un feature grande en camino). Estos documentos son la línea
> base de referencia al momento de escribirlos — no una promesa de que el
> código se va a quedar así.
