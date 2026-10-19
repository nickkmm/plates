# Respaldos

## Automático (desde 2026-10-19)

`scripts/backup_plates.sh` corre cada noche a las **02:45** en el VPS (cron de `root`, junto al respaldo de informes de las 02:30) y guarda en `/var/backups/plates/`:

| Archivo | Contenido |
|---|---|
| `plates_db_<fecha>_<hora>.dump` | Base de datos Postgres completa (`pg_dump -Fc`). Se restaura con `pg_restore`. |
| `plates_uploads_<fecha>_<hora>.tar.gz` | Volumen `plates_plates_uploads`: firmas de las declaraciones y cualquier archivo subido. |

- Cada respaldo se valida antes de conservarse (el dump debe poder listarse con `pg_restore -l`; el `.tar.gz` pasa `gzip -t`). Un respaldo fallido no queda como válido.
- Se conservan **30 días**; los más viejos se borran solos.
- El resultado de cada corrida queda en `/var/log/backup_plates.log`.
- Para correrlo a mano: `/opt/plates/scripts/backup_plates.sh`.

## Límite conocido

Los respaldos están en **el mismo disco del VPS**: protegen contra un error de datos o una migración fallida, pero no contra perder el servidor completo. Falta una copia fuera del servidor.

## Antes de cada migración

Sigue siendo costumbre hacer además un dump manual con nombre `/root/plates_backup_<fecha>_pre_<cambio>.dump` antes de desplegar una migración.
