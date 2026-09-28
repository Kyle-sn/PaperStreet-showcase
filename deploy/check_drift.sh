#!/usr/bin/env bash
# Detects drift between this repo's deploy/ configs and what's actually live
# on the host, plus a few expected-value spot-checks on jts.ini (Gateway's
# own state file, which is host-generated and NOT copy-installed -- see
# MANIFEST's header note). Alerts to Discord on any drift; silent otherwise.
# Intended to run daily via a systemd timer (see systemd/*-drift-check.timer
# in this same directory).
#
# Usage: ./deploy/check_drift.sh <vm|pi>
# Run from a checkout of this repo on the target host itself.
set -uo pipefail

HOST_ROLE="${1:?usage: check_drift.sh <vm|pi>}"
case "$HOST_ROLE" in
  vm|pi) ;;
  *) echo "check_drift.sh: host must be 'vm' or 'pi', got '$HOST_ROLE'" >&2; exit 1 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="$SCRIPT_DIR/MANIFEST"

drifted=()

while read -r host mode src dest; do
  [[ -z "$host" || "$host" == \#* ]] && continue
  [[ "$host" != "$HOST_ROLE" ]] && continue

  src_path="$SCRIPT_DIR/$src"
  if [[ ! -f "$dest" ]]; then
    drifted+=("$dest (missing on host)")
  elif ! cmp -s "$src_path" "$dest"; then
    drifted+=("$dest (differs from deploy/$src)")
  fi
done < "$MANIFEST"

if [[ "$HOST_ROLE" == "vm" ]]; then
  JTS_INI="/home/ubuntu/Jts/jts.ini"
  if [[ -f "$JTS_INI" ]]; then
    # jts.ini is CRLF (Gateway's own format, even on Linux) -- strip \r so a
    # plain ^KEY=VALUE$ anchor still matches.
    jts_unix="$(tr -d '\r' < "$JTS_INI")"
    grep -q '^TimeZone=America/New_York$' <<<"$jts_unix" || drifted+=("$JTS_INI: TimeZone is not America/New_York")
    grep -q '^tradingMode=p$' <<<"$jts_unix" || drifted+=("$JTS_INI: tradingMode is not paper (p)")
    grep -q '^ApiOnly=true$' <<<"$jts_unix" || drifted+=("$JTS_INI: ApiOnly is not true")
  else
    drifted+=("$JTS_INI: file not found")
  fi
fi

if [[ "${#drifted[@]}" -eq 0 ]]; then
  logger -t paperstreet-check-drift "ok: no drift on $HOST_ROLE"
  exit 0
fi

message="Config drift detected on $HOST_ROLE:"$'\n'
for item in "${drifted[@]}"; do
  message+="- ${item}"$'\n'
done
logger -t paperstreet-check-drift "$message"

send_discord() {
  local url="$1"
  [[ -n "$url" ]] || return 0
  local escaped
  escaped="$(printf '%s' "$message" | sed 's/"/\\"/g; s/$/\\n/' | tr -d '\n')"
  curl -sS -m 5 -H "Content-Type: application/json" \
    -d "{\"content\": \"**drift_detected** (${HOST_ROLE})\\n${escaped}\"}" \
    "$url" >/dev/null || true
}

if [[ "$HOST_ROLE" == "vm" ]]; then
  webhook_url="$(aws ssm get-parameter --name /paperstreet/alerts/discord_webhook_url \
    --with-decryption --region us-east-1 --query Parameter.Value --output text 2>/dev/null || true)"
else
  WEBHOOK_ENV_FILE="/etc/paperstreet/discord-webhook.env"
  [[ -f "$WEBHOOK_ENV_FILE" ]] && source "$WEBHOOK_ENV_FILE"
  webhook_url="${DISCORD_WEBHOOK_URL:-}"
fi

send_discord "$webhook_url"
exit 1
