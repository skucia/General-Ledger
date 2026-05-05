# General Ledger

A simple General Ledger web app. Backend: Python + FastAPI + psycopg v3. Frontend: server-rendered Jinja2 (added in Phase 2).

This README covers Phase 1 (project skeleton, schema migration, bootstrap admin user). It will grow as later phases land.

---

## Prerequisites

- Python 3.11 or newer (`python3 --version`)
- Postgres 17 already running locally with these credentials (already true on this machine):
  - host `localhost`, port `5432`, database `generalledger`, user `gluser`, password `<REDACTED>`

If you ever need to verify the DB is reachable from the host:

```bash
psql "host=localhost port=5432 dbname=generalledger user=gluser password=<REDACTED>" -c "SELECT 1"
```

---

## One-time setup

From the project root (`~/Projects/general-ledger`):

```bash
# 1. Create and activate a Python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 3. Confirm .env exists (it should already — gitignored)
ls -la .env
```

---

## Apply database migrations

This creates the four tables (`users`, `accounts`, `transactions`, `transaction_lines`) and the DR=CR balance trigger. Safe to re-run any time — it tracks which migrations have already been applied in a `schema_migrations` table.

```bash
python -m scripts.run_migrations
```

You should see `APPLY 001_initial_schema.sql ... ok` the first time, and `SKIP` on subsequent runs.

---

## Create the first Admin user (bootstrap)

The login screen (Phase 2) won't let anyone in until at least one admin exists. Run this interactive script and follow the prompts:

```bash
python -m scripts.create_admin
```

It asks for:
- Username (must be unique)
- Email
- Password (min 8 chars, typed twice, hidden)

The created user is `is_admin=TRUE`, `user_type='full'`, `must_change_password=FALSE`.

---

## Start the server

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Default URL: <http://127.0.0.1:8000/>

- Anonymous visitors are redirected to `/login`.
- After signing in, you land on `/menu` (the placeholder; Phase 3 fills it in).
- Any logged-in user can change their own password at `/change-password`.
- Admins can reset another user's password at `/admin/reset-password` — the affected user is forced to choose a new password the next time they log in.

Stop the server with `Ctrl+C`.

---

## Test credentials

Whatever you entered into `python -m scripts.create_admin` above. Login itself arrives in Phase 2.

---

## Project layout

```
general-ledger/
├── .env                  # real secrets (gitignored)
├── .env.example          # template, safe to commit
├── requirements.txt
├── uploads/              # transaction file attachments live here
├── migrations/
│   └── 001_initial_schema.sql
├── scripts/
│   ├── run_migrations.py
│   └── create_admin.py
└── app/
    ├── main.py           # FastAPI entrypoint
    ├── config.py         # loads .env into a settings object
    ├── db.py             # psycopg v3 connection helper
    ├── security.py       # bcrypt hash / verify
    ├── routers/          # route handlers (Phase 2+)
    ├── services/         # business logic (Phase 2+)
    └── templates/        # Jinja2 HTML (Phase 2+)
```
