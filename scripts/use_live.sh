#!/usr/bin/env bash
# Point .env at generalledger_live (the real-data database).
#
# Idempotent: a no-op if the live line is already uncommented.
# Refuses if .env is missing or doesn't contain both expected DB_NAME lines.
# BSD-sed compatible (sed -i '' with empty backup suffix), so works on macOS
# without GNU coreutils.

set -euo pipefail

ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"

# 1. .env must exist
[[ -f "$ENV_FILE" ]] || {
    echo "ERROR: $ENV_FILE not found" >&2
    exit 1
}

# 2. Both expected lines must be present (in commented or uncommented form)
grep -q 'DB_NAME=generalledger_live' "$ENV_FILE" || {
    echo "ERROR: $ENV_FILE doesn't contain a DB_NAME=generalledger_live line" >&2
    exit 1
}
grep -q 'DB_NAME=generalledger_test' "$ENV_FILE" || {
    echo "ERROR: $ENV_FILE doesn't contain a DB_NAME=generalledger_test line" >&2
    exit 1
}

# 3. Idempotence — already on live? say so and exit 0.
if grep -qE '^DB_NAME=generalledger_live$' "$ENV_FILE"; then
    echo "Already on live (DB_NAME=generalledger_live)."
    exit 0
fi

# 4. Swap. The test substitution preserves any trailing inline comment;
#    the live substitution tolerates 0+ spaces after the # marker.
sed -i '' \
    -e 's/^DB_NAME=generalledger_test/# DB_NAME=generalledger_test/' \
    -e 's/^# *DB_NAME=generalledger_live$/DB_NAME=generalledger_live/' \
    "$ENV_FILE"

echo "Switched to live (DB_NAME=generalledger_live)."
echo "Restart uvicorn for the change to take effect."
