# OPERATIONS.md

**Purpose:** An inventory of every external account/service PaperStreet depends on — what it's for, how it's recovered if access is lost, and what breaks if it goes down. Pointers only, no secrets. Secret values live in a password manager (human credentials) or AWS SSM Parameter Store (machine credentials) — see `DEPLOYMENT.md` §3.4.

_Trimmed for this public mirror: account numbers, usernames, key paths, resource IDs, IP addresses, bucket names, and per-row cost/MFA detail are kept in the private working repo's copy._

---

## Asset register

| Service | Purpose | Recovery path | Failure impact |
|---|---|---|---|
| IBKR — live account | Master/human account; source of the paper account's shared market-data entitlement. No live trading today | IBKR account recovery / support | No account admin access |
| IBKR — paper account | Gateway+IBC unattended login target for paper trading | IBKR account recovery / support | Engine can't authenticate; paper trading halts |
| AWS — root | Account owner; break-glass only | AWS account recovery flow | Loss of ultimate account control |
| AWS — IAM admin user | Day-to-day console/CLI access via `aws login` (short-lived creds, no static keys) | Root re-issues/resets IAM credentials | Can't manage AWS resources until root intervenes |
| AWS — EC2 instance (`t3.medium`, `us-east-1`) | Hosts IB Gateway+IBC and the PaperStreet engine | Relaunch from AMI + `deploy/`; Elastic IP is stable across relaunch | Engine and Gateway offline |
| AWS — instance IAM role | VM reads its own `/paperstreet/*` SSM parameters at boot and writes (only) to the DB backup bucket; no human logs into it | Recreate role, trust policy, and both inline policies | VM can't fetch runtime secrets or write backups; Gateway can't authenticate |
| AWS — SSM Parameter Store (`/paperstreet/...`) | Machine secrets (IBC/IBKR password, Discord webhook URL) as SecureString | Re-write the parameter value from an admin session | Same as instance role above |
| AWS — S3 backup bucket | Nightly VM `paperstreet.db` backups; versioned, ~90-day noncurrent retention. Instance role is write-only (no read/delete) | Recreate bucket + policy | Loses the automated off-VM DB recovery point; EBS snapshot and dev-box copy remain |
| AWS — daily EBS snapshot (Data Lifecycle Manager) | Whole-volume point-in-time recovery for the VM root volume, 7-day retention | Recreate the DLM policy | No fast whole-instance rollback after a bad deploy |
| Password manager | All human-interactive credentials | Password-manager account recovery | Locked out of every other credential in this table |
| Raspberry Pi 4 (home, Ubuntu Server 24.04) | Off-box log destination and dead-man's switch; reaches the VM over Tailscale | Reflash, then `deploy/install.sh pi` | Loses external liveness monitoring; a dead engine may go unnoticed |
| GitHub repo + read-only deploy keys (VM and Pi, separate) | Source of truth for code and `deploy/` config | Rotate/reissue the deploy key from repo settings | Host can't pull updates; installed config keeps running |
| Google Drive — off-site archive | Manual copy of irreplaceable data from the dev box: raw Databento pulls and periodic `paperstreet.db` snapshots | Google account recovery | Loses the only off-box copy of the paid Databento pull |
| Discord webhook | Alert channel for VM-side alerts and the Pi's dead-man's switch. Two copies of the URL: SSM (VM) and a root-owned `0600` env file (Pi) | Recreate the webhook, rewrite both copies | Alerts stop being delivered; detection/logging unaffected |

## systemd units

| Unit | Host | What it does | Failure impact |
|---|---|---|---|
| `gateway-ibc.service` | VM | Supervises IB Gateway + IBC; `Restart=always`, not boot-started | Gateway not restarted after a crash |
| `paperstreet-engine.service` | VM | Supervises `run_live.py`; `Restart=always`, not boot-started | No new signals/orders; broker-side position unaffected |
| `paperstreet-trading-{start,stop}.timer` | VM | Start/stop both services Mon–Fri 07:30/15:30 America/Chicago | Services don't come up (or don't go down) on schedule |
| `paperstreet-db-backup.timer` | VM | Nightly `scripts/backup_live_db.sh` (SQLite online backup → S3) | Backups go stale silently — failure isn't wired to Discord yet, only visible in `systemctl --failed`/journald |
| `systemd-journal-upload.service` | VM | Ships the full journal to the Pi over Tailscale | Off-box logs and the heartbeat that rides them stop |
| `systemd-journal-remote.socket`/`.service` | Pi | Receives the shipped journal (plain HTTP over the tailnet, `ufw`-restricted to the VM) | Log shipping and the dead-man's switch go blind |
| `paperstreet-deadmans-switch.timer` | Pi | Every 60s, checks for recent engine log activity inside the trading window; alerts on ok↔stale | Nothing pages the operator on heartbeat loss |
| `paperstreet-check-drift.timer` | VM and Pi | Daily diff of live host config against `deploy/`; alerts on drift | Config drift goes undetected |

---

## Notes

- Operational gotchas worth knowing before touching the hosts: never use `ps aux`, `pgrep -a`, or `systemctl status gateway-ibc` to check on Gateway — IBC passes the password as JVM argv, so anything that prints command lines exposes it. Use `pgrep -x`, `ss -tlnp`, `systemctl show --property=`, or `journalctl`. See `DEPLOYMENT_INCIDENTS.md`.
- See also: `DEPLOYMENT.md` §3.4 (credentials & identity decisions), §6.4 (backup classification).
