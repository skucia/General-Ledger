#!/usr/bin/env bash
# Point .env at generalledger_test (the frozen test-fixture database).
#
# Idempotent: a no-op if the test line is already uncommented.
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

# 3. Idempotence — already on test? say so and exit 0. The trailing-comment-
#    tolerant prefix match is needed because the test line normally carries
#    an inline comment we want to leave alone.
if grep -qE '^DB_NAME=generalledger_test([[:space:]]|$)' "$ENV_FILE"; then
    echo "Already on test (DB_NAME=generalledger_test)."
    exit 0
fi

# 4. Swap. The live substitution uses an end-of-line anchor (no inline comment
#    expected on the live line); the test substitution does NOT anchor at end-
#    of-line, so any trailing inline comment on the test line is preserved.
sed -i '' \
    -e 's/^DB_NAME=generalledger_live$/# DB_NAME=generalledger_live/' \
    -e 's/^# *DB_NAME=generalledger_test/DB_NAME=generalledger_test/' \
    "$ENV_FILE"

echo "Switched to test (DB_NAME=generalledger_test)."
echo "Restart uvicorn for the change to take effect."
