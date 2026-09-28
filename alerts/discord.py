"""
discord.py

`send_alert()` is the engine's one path to the operator's Discord channel
(DEPLOYMENT.md 7.3): kill-switch trips (risk/gate.py), hard IBKR order
rejections (ib_app.py), stuck reconnects and unhandled trading-loop exceptions
(run_live.py). Alerting is best-effort auxiliary behavior, not core trading
logic -- a Discord outage or a missing webhook secret must never take down the
trading loop, so every failure here is caught and logged, never raised.

Webhook URL resolution: `DISCORD_WEBHOOK_URL` env var first (local dev, per
DEPLOYMENT.md 3.4's .env convention), else AWS SSM Parameter Store
(`/paperstreet/alerts/discord_webhook_url`, SecureString, instance-role read --
same pattern as the IBC password in `~/IBC/start-with-ssm.sh`). Cached after
the first successful resolution so a steady stream of alerts doesn't mean a
steady stream of SSM calls.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

from utils.log_config import setup_logger

logger = setup_logger(__name__)

SSM_PARAM_NAME = "/paperstreet/alerts/discord_webhook_url"
SSM_REGION = "us-east-1"
REQUEST_TIMEOUT_SECONDS = 5

_webhook_url_cache: str | None = None
_last_sent: dict[str, float] = {}


def _get_webhook_url() -> str | None:
    global _webhook_url_cache
    if _webhook_url_cache is not None:
        return _webhook_url_cache

    env_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if env_url:
        _webhook_url_cache = env_url
        return _webhook_url_cache

    import boto3  # imported lazily: only the live VM path needs it

    client = boto3.client("ssm", region_name=SSM_REGION)
    response = client.get_parameter(Name=SSM_PARAM_NAME, WithDecryption=True)
    _webhook_url_cache = response["Parameter"]["Value"]
    return _webhook_url_cache


def send_alert(kind: str, message: str, min_interval_s: float = 300.0) -> None:
    """Post `message` to the Discord webhook, tagged with `kind`.

    Rate-limited per `kind`: a repeat of the same `kind` within
    `min_interval_s` is dropped rather than sent, so a condition that keeps
    retrying (e.g. an exception hit every 5s) doesn't spam the channel. The
    rate-limit clock starts on the attempt, not on a successful send, so a
    down webhook doesn't turn into a tight retry loop either.
    """
    now = time.time()
    last = _last_sent.get(kind, 0.0)
    if now - last < min_interval_s:
        return
    _last_sent[kind] = now

    try:
        webhook_url = _get_webhook_url()
        if not webhook_url:
            logger.warning(f"No Discord webhook configured; dropping alert|kind={kind}")
            return

        body = json.dumps({"content": f"**{kind}**: {message}"}).encode("utf-8")
        request = urllib.request.Request(
            webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS)
    except Exception as e:
        logger.warning(f"Failed to send Discord alert|kind={kind}|error={e}")
