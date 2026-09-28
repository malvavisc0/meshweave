# Backup and restore

Postgres holds accounts, API keys, funnel history, and crawl data — losing
the `postgres_data` volume loses the business. Backups are plain
`pg_dump` output, compressed, taken from the running container.

## Backup

`scripts/backup-postgres.sh` dumps the prod database through the compose
container. Run it from the server's checkout directory (where
`docker-compose.prod.yaml` and `.env.prod` live):

```sh
MESHWEAVE_BACKUP_DIR=/var/backups/meshweave \
POSTGRES_USER=meshweave \
bash scripts/backup-postgres.sh
```

Reads `.env.prod` values for `POSTGRES_USER`/`POSTGRES_DB` from the
environment; set them (or source `.env.prod`) before calling. Schedule
nightly via cron, e.g.:

```
0 3 * * * cd /srv/meshweave && set -a && . ./.env.prod && set +a && bash scripts/backup-postgres.sh
```

Backups older than `MESHWEAVE_BACKUP_RETENTION_DAYS` (default 14) are
deleted after each run.

## Restore

Backups are written to `$MESHWEAVE_BACKUP_DIR/meshweave-<stamp>.sql.gz`
(default `/var/backups/meshweave`). The commands below use `$POSTGRES_USER`
from the environment — `set -a && . ./.env.prod && set +a` (source
`.env.prod`) in the shell first.

1. Stop the app so no writes race the restore:

   ```sh
   docker compose -f docker-compose.prod.yaml --env-file .env.prod stop webapp
   ```

2. Restore into a **scratch** database first and verify it (never test a
   restore on the live database):

   ```sh
   docker compose -f docker-compose.prod.yaml --env-file .env.prod exec -T postgres \
     psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE meshweave_restore"
   gunzip -c "$MESHWEAVE_BACKUP_DIR/meshweave-<stamp>.sql.gz" | \
     docker compose -f docker-compose.prod.yaml --env-file .env.prod exec -T postgres \
       psql -U "$POSTGRES_USER" -d meshweave_restore
   ```

   Check row counts and recent crawls look right.

3. Restore into the live database (drops current data):

   ```sh
   docker compose -f docker-compose.prod.yaml --env-file .env.prod exec -T postgres \
     psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE meshweave"
   docker compose -f docker-compose.prod.yaml --env-file .env.prod exec -T postgres \
     psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE meshweave"
   gunzip -c "$MESHWEAVE_BACKUP_DIR/meshweave-<stamp>.sql.gz" | \
     docker compose -f docker-compose.prod.yaml --env-file .env.prod exec -T postgres \
       psql -U "$POSTGRES_USER" -d meshweave
   ```

4. Start the app:

   ```sh
   docker compose -f docker-compose.prod.yaml --env-file .env.prod start webapp
   ```

## The launch database

The launch database is created **fresh** from the single migration
`880764c4c4b4` — it starts empty of crawls, and no historical purge ever
ran. Never restore a pre-launch dump over it: those dumps carry
historical crawls and pre-answerability-fix scores the fresh schema never
had. If one was restored over it anyway, resync revision tracking with
`alembic stamp --purge 880764c4c4b4`, then recreate the database from a
post-launch backup instead.
