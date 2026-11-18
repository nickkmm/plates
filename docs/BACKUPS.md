# Backups

## Automatic (since 2026-10-19)

`scripts/backup_plates.sh` runs every night at **02:45** on the VPS (`root` cron, alongside the 02:30 report backup) and saves to `/var/backups/plates/`:

| File | Contents |
|---|---|
| `plates_db_<fecha>_<hora>.dump` | Full Postgres database (`pg_dump -Fc`). Restore with `pg_restore`. |
| `plates_uploads_<fecha>_<hora>.tar.gz` | The `plates_plates_uploads` volume: declaration signatures and any other uploaded file. |

- Each backup is validated before it is kept (the dump must be listable with `pg_restore -l`; the `.tar.gz` must pass `gzip -t`). A failed backup is not kept as valid.
- Backups are kept for **30 days**; older ones are deleted automatically.
- The result of each run is recorded in `/var/log/backup_plates.log`.
- To run it by hand: `/opt/plates/scripts/backup_plates.sh`.

## Known limitation

The backups are on **the same VPS disk**: they protect against a data error or a failed migration, but not against losing the whole server. An off-server copy is still missing.

## Before every migration

It is still customary to also take a manual dump named `/root/plates_backup_<fecha>_pre_<cambio>.dump` before deploying a migration.
