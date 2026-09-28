#!/bin/bash
# Dead-man's switch (docs/DEPLOYMENT.md §7.2): alerts if the VM's
# engine has stopped logging (via the shipped journal, step 37) for longer
# than STALE_THRESHOLD_SECONDS. Edge-triggered (alerts on the ok->stale and
# stale->ok transitions) plus a periodic reminder while stuck stale, so an
# extended outage doesn't spam Discord every single run.
#
# The engine is deliberately stopped outside trading hours
# (paperstreet-trading-{start,stop}.timer on the VM: up 07:30-15:30
# America/Chicago, Mon-Fri) -- without MONITOR_* below, this would alert
# every weeknight at ~3:33pm CT and every 15 min all night/weekend, since
# "no log activity" is exactly what a deliberate stop looks like. Alerting
# is suppressed outside the monitoring window; state (and the eventual
# heartbeat_recovered) still tracks correctly across the gap.
set -uo pipefail

JOURNAL_FILE="/var/log/journal/remote/remote-<VM_TAILSCALE_IP>.journal"
UNIT="paperstreet-engine.service"
STALE_THRESHOLD_SECONDS=180
REMINDER_INTERVAL_SECONDS=900
WEBHOOK_ENV_FILE="/etc/paperstreet/discord-webhook.env"
STATE_FILE="${STATE_DIRECTORY:-/var/lib/paperstreet}/deadmans_switch_state"

# Must track paperstreet-trading-start.timer/paperstreet-trading-stop.timer
# on the VM (deploy/vm/systemd/) -- 5-min grace after start covers Gateway
# login + engine reconnect latency (observed ~25s in practice) so a normal
# morning start isn't flagged as a failure before it's had a chance to log.
MONITOR_TZ="America/Chicago"
MONITOR_START_HHMM="0735"
MONITOR_STOP_HHMM="1530"

[ -f "$WEBHOOK_ENV_FILE" ] && source "$WEBHOOK_ENV_FILE"

dow="$(TZ="$MONITOR_TZ" date +%u)"      # 1=Mon .. 7=Sun
hhmm="$(TZ="$MONITOR_TZ" date +%H%M)"
# 10# forces base-10: a plain leading-zero "0800" would otherwise be parsed
# as (invalid) octal by bash arithmetic and abort the script at 8/9am/pm.
hhmm_num=$((10#$hhmm))
start_num=$((10#$MONITOR_START_HHMM))
stop_num=$((10#$MONITOR_STOP_HHMM))

in_monitoring_window=false
if [ "$dow" -le 5 ] && [ "$hhmm_num" -ge "$start_num" ] && [ "$hhmm_num" -lt "$stop_num" ]; then
    in_monitoring_window=true
fi

last_ts_us="$(journalctl --file "$JOURNAL_FILE" -u "$UNIT" -n 1 -o json --no-pager 2>/dev/null \
    | jq -r '.__REALTIME_TIMESTAMP // empty' 2>/dev/null)"
now_us=$(( $(date +%s) * 1000000 ))

if [ -z "$last_ts_us" ]; then
    age_seconds="$STALE_THRESHOLD_SECONDS"
else
    age_seconds=$(( (now_us - last_ts_us) / 1000000 ))
fi

prev_state="ok"
last_alert_epoch=0
if [ -f "$STATE_FILE" ]; then
    # shellcheck disable=SC1090
    source "$STATE_FILE"
fi

current_state="ok"
[ "$age_seconds" -ge "$STALE_THRESHOLD_SECONDS" ] && current_state="stale"
now_epoch="$(date +%s)"

send_discord() {
    local kind="$1" message="$2"
    logger -t paperstreet-deadmans-switch "${kind}: ${message}"
    [ -n "${DISCORD_WEBHOOK_URL:-}" ] || return 0
    curl -sS -m 5 -H "Content-Type: application/json" \
        -d "{\"content\": \"**${kind}**: ${message}\"}" \
        "$DISCORD_WEBHOOK_URL" >/dev/null || true
}

if [ "$current_state" = "stale" ]; then
    if [ "$in_monitoring_window" = true ] && { [ "$prev_state" = "ok" ] || [ $(( now_epoch - last_alert_epoch )) -ge "$REMINDER_INTERVAL_SECONDS" ]; }; then
        send_discord "heartbeat_loss" "No log activity from ${UNIT} in ${age_seconds}s (threshold ${STALE_THRESHOLD_SECONDS}s)"
        last_alert_epoch="$now_epoch"
    fi
elif [ "$prev_state" = "stale" ]; then
    send_discord "heartbeat_recovered" "${UNIT} log activity resumed"
    last_alert_epoch="$now_epoch"
fi

printf 'prev_state=%s\nlast_alert_epoch=%s\n' "$current_state" "$last_alert_epoch" > "$STATE_FILE"
