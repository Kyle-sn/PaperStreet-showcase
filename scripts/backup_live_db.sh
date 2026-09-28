#!/usr/bin/env bash
# Nightly off-VM backup of the live PaperStreet SQLite DB.
#
# Uses SQLite's own online backup API (the same mechanism as the `sqlite3 .backup`
# CLI command) rather than a raw file copy, since the engine may hold the DB open
# in WAL mode -- a plain `cp` can capture it mid-write and land a corrupt file.
#
# Auth: relies entirely on the EC2 instance role via the AWS CLI's default
# credential chain. No access keys are read, stored, or passed here.
set -euo pipefail

DB_PATH="${PAPERSTREET_DB_PATH:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/data/paperstreet.db}"
BUCKET="${PAPERSTREET_BACKUP_BUCKET:?PAPERSTREET_BACKUP_BUCKET must be set (see docs/OPERATIONS.md)}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TMP_BACKUP="$(mktemp --suffix=.db)"

cleanup() { rm -f "$TMP_BACKUP"; }
trap cleanup EXIT

if [[ ! -f "$DB_PATH" ]]; then
    echo "backup_live_db: no DB at $DB_PATH, nothing to back up" >&2
    exit 1
fi

python3 - "$DB_PATH" "$TMP_BACKUP" <<'PYEOF'
import sqlite3
import sys

src_path, dst_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(src_path)
dst = sqlite3.connect(dst_path)
with dst:
    src.backup(dst)
src.close()
dst.close()
PYEOF

# Fails loudly (set -e) if the CLI or credentials aren't available -- systemd then
# marks the run failed and it shows up in `systemctl --failed` / journald.
#
# TODO: this failure is not yet routed to the off-box Pi heartbeat / Discord
# alert path; a failed run is only visible on the VM itself via
# `systemctl --failed` / journald (see docs/OPERATIONS.md).
aws s3 cp "$TMP_BACKUP" "s3://${BUCKET}/paperstreet-db/paperstreet_${STAMP}.db"

echo "backup_live_db: uploaded s3://${BUCKET}/paperstreet-db/paperstreet_${STAMP}.db"
