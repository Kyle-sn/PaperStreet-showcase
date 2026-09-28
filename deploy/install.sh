#!/usr/bin/env bash
# Installs this repo's deploy/ configs onto the host by copy, per MANIFEST.
#
# Deliberately copy, never symlink: a symlink into the repo means a later
# `git pull` silently changes what's live. A copy only takes effect the next
# time this script is run by hand, so drift between "what's in git" and
# "what's live" is visible (via check_drift.sh) rather than automatic.
#
# Deliberately no restart/enable/start of any unit here. Placing a new unit
# file (or overwriting an existing one) and reloading systemd's view of it is
# safe and idempotent; restarting gateway-ibc/paperstreet-engine/anything
# else is an operational decision with real consequences (killed session,
# interrupted trading loop) that the operator should trigger explicitly.
#
# Usage: sudo ./deploy/install.sh <vm|pi>
# Run from a checkout of this repo on the target host itself.
set -euo pipefail

HOST_ROLE="${1:?usage: install.sh <vm|pi>}"
case "$HOST_ROLE" in
  vm|pi) ;;
  *) echo "install.sh: host must be 'vm' or 'pi', got '$HOST_ROLE'" >&2; exit 1 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="$SCRIPT_DIR/MANIFEST"

changed=0
while read -r host mode src dest; do
  [[ -z "$host" || "$host" == \#* ]] && continue
  [[ "$host" != "$HOST_ROLE" ]] && continue

  src_path="$SCRIPT_DIR/$src"
  if [[ ! -f "$src_path" ]]; then
    echo "install.sh: missing source file $src_path" >&2
    exit 1
  fi

  dest_dir="$(dirname "$dest")"
  mkdir -p "$dest_dir"
  if ! cmp -s "$src_path" "$dest"; then
    install -m "$mode" "$src_path" "$dest"
    echo "installed: $dest"
    changed=$((changed + 1))
  fi
done < "$MANIFEST"

if [[ "$changed" -gt 0 ]]; then
  systemctl daemon-reload
  echo "install.sh: $changed file(s) changed, ran systemctl daemon-reload"
  echo "install.sh: no units were restarted -- restart/enable the affected ones by hand"
else
  echo "install.sh: nothing changed"
fi
