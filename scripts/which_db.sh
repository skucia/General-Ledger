#!/usr/bin/env bash
# Report which database .env currently points at.
#
# Read-only — never modifies .env. Reports a friendly label for the standard
# values (generalledger_live / generalledger_test) and a generic "(custom)"
# label for anything else (e.g. if the user manually pointed .env at a
# different name). Exits non-zero with a clear warning if the .env state is
# ambiguous (no uncommented DB_NAME, or multiple uncommented).

set -euo pipefail

ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"

[[ -f "$ENV_FILE" ]] || {
    echo "ERROR: $ENV_FILE not found" >&2
    exit 1
}

# Count uncommented DB_NAME lines. `|| true` because grep -c exits non-zero
# when there are no matches, which set -e would otherwise treat as fatal.
count=$(grep -cE '^DB_NAME=' "$ENV_FILE" || true)

case "$count" in
    0)
        echo "WARN: no uncommented DB_NAME line in $ENV_FILE — app would fail to start." >&2
        exit 1
        ;;
    1)
        ;;
    *)
        echo "WARN: $count uncommented DB_NAME lines in $ENV_FILE — last wins; ambiguous." >&2
        exit 1
        ;;
esac

active_line=$(grep -E '^DB_NAME=' "$ENV_FILE" | head -1)
# Read the first whitespace-separated token (the value), discard the rest
# (any trailing inline comment after the value). The two-variable form is
# what makes `read` actually split on IFS — single-variable `read` would
# capture the whole line.
read -r active_value _rest <<< "${active_line#DB_NAME=}"

case "$active_value" in
    generalledger_live)
        echo "Active database: $active_value (real data)"
        ;;
    generalledger_test)
        echo "Active database: $active_value (frozen test fixture)"
        ;;
    *)
        echo "Active database: $active_value (custom)"
        ;;
esac
