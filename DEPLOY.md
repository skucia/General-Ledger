# Deploy

How to run the General Ledger app in its three supported modes and how
to operate it on the Hostinger VPS. Assumes Docker + Docker Compose v2
are installed (already true on the VPS; on macOS install Docker Desktop).

Every mode reads config from `.env`. `.env.example` shows the full
variable list. **Never commit `.env`** — `.gitignore` keeps it out.

---

## Mode A — Local dev against the existing standalone Postgres

Everyday development. Postgres is the standalone `gl-postgres` container
you already run on the host; this just brings up the FastAPI app in
Docker and points it at the host's Postgres.

1. In `.env`, set:
   ```
   DB_HOST=host.docker.internal
   ```
   Leave the rest of the values as they are for local dev.
2. Build and run the app container:
   ```bash
   docker compose up app --build
   ```
3. Visit `http://localhost:8000/login`.

The standalone `gl-postgres` container is untouched. `docker compose up
app` does NOT start the bundled `postgres` service (it sits behind the
`fullstack` profile).

---

## Mode B — Full-stack local test

Smoke-test the whole stack the way it'll run on the VPS, but on your
laptop. Brings up app + a separate test Postgres in the same compose
network. The test Postgres uses the named volume `gl_pgdata_test` —
isolated from your standalone `gl-postgres`.

1. In `.env`, set:
   ```
   DB_HOST=postgres
   ```
2. Start both services:
   ```bash
   docker compose --profile fullstack up --build
   ```
3. First-time only: the test Postgres comes up empty. Run migrations
   and create an admin user from inside the app container:
   ```bash
   docker compose exec app python scripts/run_migrations.py
   docker compose exec app python scripts/create_admin.py
   ```
4. Visit `http://localhost:8000/login`.

To tear down (keeping the data volume): `docker compose --profile
fullstack down`. To wipe the test data too: add `-v` to that command.

---

## Mode C — First deploy to the Hostinger VPS

Run once per VPS, then use the "code update" recipe below for ongoing
deploys.

1. SSH to the VPS.
2. Clone the repo to a directory of your choice:
   ```bash
   git clone https://github.com/skucia/General-Ledger.git
   cd General-Ledger
   ```
3. Create a production `.env`:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and set:
   - `DB_HOST=postgres`
   - `DB_NAME`, `DB_USER`, `DB_PASSWORD` — pick fresh, strong values
   - `SESSION_SECRET` — generate with:
     ```bash
     python3 -c "import secrets; print(secrets.token_urlsafe(48))"
     ```
   - `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCKOUT_MINUTES` — leave defaults or
     adjust
   - `UPLOAD_DIR=/app/uploads`
4. Open port 8000 in the Hostinger firewall (Hostinger panel → VPS →
   Firewall). The VPS's Traefik is already bound to port 80; we leave
   it alone and run the app on 8000.
5. Build and start in the background:
   ```bash
   docker compose --profile fullstack up -d --build
   ```
6. Wait a few seconds for Postgres to pass its healthcheck, then run
   migrations and create the first admin user:
   ```bash
   docker compose exec app python scripts/run_migrations.py
   docker compose exec app python scripts/create_admin.py
   ```
7. Visit `http://<vps-ip>:8000/login`.

---

## View logs

```bash
docker compose logs -f app
```

`-f` follows. Drop it for a one-shot dump. For both services in one
stream: `docker compose --profile fullstack logs -f`.

---

## Restart after a code update

On the VPS:

```bash
git pull
docker compose --profile fullstack up -d --build
```

`--build` rebuilds the app image when source files have changed.
`-d` keeps it running in the background. The named volumes
(`gl_uploads`, `gl_pgdata_test`) survive the rebuild — your data and
uploaded files persist.

If a migration was added in the update:

```bash
docker compose exec app python scripts/run_migrations.py
```

---

## Back up the production database

Manual `pg_dump` to a local SQL file:

```bash
# from inside the project dir on the VPS
docker compose exec -T postgres pg_dump -U $DB_USER $DB_NAME > backup-$(date +%Y%m%d).sql
```

`-T` disables pseudo-TTY allocation (lets the redirect work). The file
lands in the current dir on the VPS host — `scp` it down to your laptop
to keep an off-VPS copy.

To restore (warning — destructive; wipes the current DB first):

```bash
docker compose exec -T postgres psql -U $DB_USER $DB_NAME < backup-YYYYMMDD.sql
```

---

## Back up the uploads volume

Uploaded attachment files live inside the `gl_uploads` named volume,
not in the repo or the database. Tar the volume contents to a file:

```bash
docker run --rm \
  -v gl_uploads:/data \
  -v "$(pwd)":/backup \
  alpine tar czf /backup/uploads-$(date +%Y%m%d).tar.gz -C /data .
```

`scp` the resulting tarball off the VPS to keep it. To restore:

```bash
docker run --rm \
  -v gl_uploads:/data \
  -v "$(pwd)":/backup \
  alpine tar xzf /backup/uploads-YYYYMMDD.tar.gz -C /data
```

---

## Troubleshooting

- **App container restarts in a loop.** Almost always a config error.
  Check `docker compose logs app` — `SESSION_SECRET` too short and
  Postgres connection failures both fail loudly with a clear message.
- **`host.docker.internal` not resolving on Linux.** The compose file
  already wires `extra_hosts: ["host.docker.internal:host-gateway"]`,
  which is the standard Linux workaround. Re-up the app container
  (`docker compose up app --force-recreate`) if you just added it.
- **Permission errors writing to `/app/uploads`.** The image creates
  the dir owned by the non-root `app` user. If you ever recreate the
  volume from scratch the first mount inherits those perms. If perms
  drift, `docker compose exec -u root app chown -R app:app
  /app/uploads`.
