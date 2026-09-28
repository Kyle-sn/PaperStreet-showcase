# DEPLOYMENT.md

**Status:** Running. The VM+Pi paper-trading infrastructure described below is up: Gateway+IBC and the engine are supervised on the VM, logs and a heartbeat ship to the Pi over Tailscale, and a dead-man's switch alerts to Discord. **No live capital is deployed and none is currently planned** — that is a separate decision on the trading side (strategy validation, capital allocation), not part of this document's scope.
**Scope:** How PaperStreet runs unattended for continuous live/paper operation. Covers broker connectivity, host, process supervision, observability, and state recovery. Does **not** cover strategy logic, backtesting, or capital allocation.

Items marked **[DECISION]** are configured defaults, not hard constraints. Items marked **[REQUIREMENT]** are hard constraints that must hold before any live capital is deployed.

---

## 1. Motivation

The development setup (TWS GUI + scripts on a local Windows machine) is adequate for interactive testing only. It is unfit for continuous unattended operation for three structural reasons:

1. **TWS is GUI-bound.** IBKR force-logs-off TWS on a daily schedule and surfaces modal dialogs (updates, reauth) that assume a human operator. A headless system cannot depend on a process that expects a click.
2. **Shared-fate host.** A desktop OS used for other purposes couples the strategy's uptime to OS updates, sleep, reboots, and operator error. A system holding overnight/multi-day positions must run on a host that is never touched for anything else. (Currently moot in practice — §10's market-hours-only operation means nothing is held or connected overnight — but still the reason a dedicated host is the target architecture, not a Windows desktop.)
3. **No supervision or observability.** A silently dead process is the single most likely way real capital is lost in the first year of live operation — more likely than any strategy flaw. The system must detect and alert on its own failure.

**Timescale note:** PaperStreet targets medium-frequency strategies (minutes to weeks). Latency, colocation, and proximity to IBKR's matching engine are irrelevant at this timescale and are explicitly *not* optimization targets. Reliability and correct state recovery dominate. Do not carry HFT infrastructure instincts into these decisions.

---

## 2. Target Architecture (summary)

```
┌─────────────────────────────────────────────────┐
│  Headless Linux VM (US-East cloud)               │
│                                                  │
│  ┌────────────────────┐   ┌────────────────────┐│
│  │ IB Gateway + IBC   │   │ PaperStreet engine ││
│  │ (systemd service)  │◄──┤ (systemd service)  ││
│  │ headless, auto-    │   │ connects via       ││
│  │ login, auto-restart│   │ localhost          ││
│  └────────────────────┘   └─────────┬──────────┘│
│                                      │           │
│                            ┌─────────▼──────────┐│
│                            │ heartbeat +        ││
│                            │ reconciliation     ││
│                            └─────────┬──────────┘│
└──────────────────────────────────────┼──────────┘
                                        │ off-box
                              ┌─────────▼──────────┐
                              │ external alerting  │
                              │ (dead-man's switch)│
                              └────────────────────┘
```

---

## 3. Broker Connectivity

### 3.1 IB Gateway, not TWS **[REQUIREMENT]**
Use **IB Gateway** (headless, no GUI) instead of TWS for all unattended operation. Same API surface, far lower resource footprint, no charting/GUI overhead. The API contract is identical to TWS, so the migration is a connectivity swap, not a code rewrite.

### 3.2 IBC for session management **[REQUIREMENT]**
Use **IBC** (IBController) to automate Gateway login and dismissal of dialogs. IBC is the standard tool for keeping Gateway alive unattended.

IBC's `AutoRestartTime` mechanism survives IBKR's daily forced logoff in-place, with no re-authentication and no Task Scheduler required. The **current configuration does not use it** — see §10. Instead, Gateway is started fresh each morning and shut down before the overnight reset window, sidestepping the daily-logoff problem rather than surviving it. Revisit `AutoRestartTime` if/when a strategy needs to hold positions overnight (§1).

**The start/stop schedule itself is owned by systemd, not IBC** (`paperstreet-trading-{start,stop}.timer`, §5.1) — `AutoLogoffTime=04:30 PM` is left set in `config.ini` as a harmless redundant backstop, but it is not what actually stops anything day to day. This is a deliberate fix, not the original design: for the first day of systemd supervision (2026-09-25/26), `gateway-ibc.service`'s `Restart=always` silently undid `AutoLogoffTime`'s tidy shutdown — IBC exited cleanly at 4:30pm ET as configured, and systemd, seeing an unexpected process exit, restarted it ~10s later and logged straight back in, keeping Gateway connected through the night both nights. A `systemctl stop` (what the timer issues) is never auto-restarted regardless of `Restart=always`, so moving the authoritative schedule to systemd's own `OnCalendar=` (IANA-timezone-aware, immune to the jts.ini-`TimeZone`-mismatch bug that made `AutoLogoffTime` itself fire 5 hours early once — see `DEPLOYMENT_INCIDENTS.md`) closes that gap for real. Both incidents are written up in `DEPLOYMENT_INCIDENTS.md`.

Gateway's `AutoLogoffTime` resolves against its own `~/Jts/jts.ini` `TimeZone` setting, not the OS timezone. This is set explicitly to `America/New_York` (DST-aware) with `AutoLogoffTime=04:30 PM`; any future host must have this checked explicitly rather than assumed from the OS or copied from another box's config.

### 3.3 Authentication **[DECISION]**
- **2FA mode (live account): IBKR Mobile push.** The market-hours-only operation (§10) makes this largely moot day-to-day: Gateway isn't running through the reset window at all, so there's no unattended reauth to survive. Still relevant for the pre-live read-only gate, since that's a genuinely live-account, always-on-during-market-hours validation. Paper login does not surface a 2FA challenge even under the live username with `TradingMode=paper`.
- Credentials: human-interactive secrets (IBKR/AWS logins, SSH passphrase) in a password manager; machine secrets the engine/IBC read unattended (IBKR password for IBC auto-login, alerting tokens) in **AWS SSM Parameter Store (SecureString)**, fetched at service start via an instance IAM role. Never committed to the repo.

### 3.4 Credentials & identity, at a glance **[DECISION]**
- **Runtime secrets (VM):** AWS SSM Parameter Store (SecureString), read at process start via the EC2 instance role — no plaintext on disk, nothing in the repo.
- **Local dev:** `.env` file, git-ignored, per `ARCHITECTURE.md` → Environment and Configuration. Not used for the VM.
- **Human credentials** (IBKR/AWS console logins, SSH passphrase): live in a password manager only, never read by code.
- **IBKR login identity [DECISION]:** the automated Gateway+IBC login is a **trading-only secondary IBKR user** — no funding or withdrawal permissions — separate from the primary/master login used for account administration. Limits the blast radius of a leaked or misused unattended credential to trading actions only.
- Full account/resource inventory (what exists, who/what logs into it, how to recover it): `OPERATIONS.md`. No secrets there either — pointers only.

---

## 4. Host

### 4.1 Cloud VM, not home server (initially) **[DECISION]**
Run on a small cloud VM rather than a home server for the initial live phase.
- **Rationale:** Uptime, power, and network reliability become the provider's problem. Residential ISP/power dropouts are silent, correlated failures that are hard to detect and expensive when they coincide with a market move — a worse expected outcome than the modest VM cost.
- **Sizing [DECISION]:** ~2 vCPU / 4 GB RAM is sufficient for MFT. Gateway is the main memory consumer.
- **Region [DECISION]:** US-East. Chosen for reliability and reasonable operator remote-access latency, **not** for fill quality (irrelevant at this timescale).
- **Provider [DECISION]:** AWS. ~$20–40/mo.

### 4.2 OS **[REQUIREMENT]**
Headless Linux. The supervision/logging/container ecosystem (systemd, journald, Docker) is native and reliable in a way it is not on Windows.

### 4.3 Home server as future option
A locally-hosted server is deferred, not rejected. Revisit only after cloud paper operation has been validated and the failure modes are understood. If adopted, it must have UPS + redundant network before holding live positions.

---

## 5. Process Supervision

### 5.1 Two separate services **[REQUIREMENT]**
Gateway+IBC and the PaperStreet engine run as **separate** systemd-supervised services.
- **Rationale:** The engine must be restartable without tearing down Gateway's authenticated session, and Gateway must be restartable (daily) without corrupting engine state. Coupling them defeats both.
- Both units configured with automatic restart on crash (`Restart=always`), but neither is boot-started (no `[Install]` section) — they're started and stopped on a schedule instead (below), and a crash-restart never fights that schedule because a deliberate `systemctl stop` is never treated as a crash.
- **Trading-hours schedule [DECISION]:** `paperstreet-trading-start.timer`/`paperstreet-trading-stop.timer` run `systemctl start`/`stop` on both units at 7:30am / 3:30pm America/Chicago, Mon–Fri. This is the actual on/off switch — see §3.2 for why it lives here instead of in IBC's own `AutoLogoffTime`.

### 5.2 Containerization **[DECISION — deferred]**
Docker-ize the deployment once the systemd setup is stable, to make the host reproducible and disposable. Not yet done.

### 5.3 Host config version control **[DECISION]**
Host-only config that previously existed solely as hand-run commands in the original build checklist and Pi setup notes (IBC's `config.ini`, both hosts' systemd units, journald/`journal-upload`/`journal-remote` drop-ins, and the Pi's dead-man's switch script) is now version-controlled in the repo under `deploy/` (`deploy/vm/`, `deploy/pi/`, indexed by `deploy/MANIFEST`).
- **Install by copy, never symlink** (`deploy/install.sh <vm|pi>`): a symlink into the repo would mean a routine `git pull` silently changes what's live. A copy only takes effect the next time `install.sh` is run by hand, so drift between committed and live is visible rather than automatic. `install.sh` reloads systemd (`daemon-reload`) after copying but never restarts, enables, or starts anything — that stays an explicit, deliberate operator action, consistent with §5.1's restart-independence goal.
- **Drift detection** (`deploy/check_drift.sh <vm|pi>`, run daily via `paperstreet-check-drift.timer` on each host): diffs live files against `deploy/`, plus a few expected-key spot-checks on Gateway's own `jts.ini` (host-generated, not copy-installed). Alerts to Discord on drift, via the same per-host webhook path as the existing alerts (§7.3): SSM on the VM, the local `/etc/paperstreet/discord-webhook.env` on the Pi.
- **Reverses an earlier Pi-setup decision** (which kept the dead-man's switch script untracked/host-only to avoid a second deploy key "for one script"). The Pi now has its own read-only GitHub deploy key and a full repo clone, since the set of Pi-side files worth versioning grew beyond that one script once `check_drift.sh` itself needed something to diff against and a way to reach the Pi.
- **Public-mirror placeholders.** In this repo, host-specific values in `deploy/` are replaced with placeholders: `<VM_TAILSCALE_IP>` / `<PI_TAILSCALE_IP>` (tailnet addresses in the journal-upload URL and the dead-man's switch's journal path) and `<BACKUP_BUCKET>` (the S3 bucket name in `paperstreet-db-backup.service`). Substitute real values before running `install.sh`, or `check_drift.sh` will (correctly) report those files as drifted.
- `deploy/*.service`/`*.timer` unit files are checked in CI via `systemd-analyze verify` (see the `systemd-verify` GitHub Actions workflow) against a stubbed `/home/ubuntu/...` layout, so a syntax error or a bad `ExecStart` path is caught before it ever reaches a host.

---

## 6. State Recovery and Correctness

This section is the highest-risk area and the most important to get right.

### 6.1 Broker is source of truth **[REQUIREMENT]**
On startup and after any reconnection, the engine reconstructs its view of positions, open orders, and cash **from IBKR**, never from local memory or persisted local state. Local state may be used as a cross-check, never as the authority.

### 6.2 Graceful connection-loss handling **[REQUIREMENT]**
The engine must tolerate connection loss as a normal event, not an exception. Gateway's daily restart, API disconnects, and IBKR maintenance windows disconnect the engine routinely. On disconnect the engine must:
1. Stop acting on stale signals.
2. Reconnect (with backoff).
3. Re-sync positions / open orders / cash from the broker.
4. Reconcile against expected state before resuming trading.

### 6.3 Daily reconciliation **[REQUIREMENT]**
A scheduled check compares what the engine believes it holds (positions + cash) against what IBKR reports. Any divergence beyond a defined tolerance halts trading and alerts the operator. Reconciliation failure is treated as a system fault, not a warning.

### 6.4 Data durability & backup classification **[DECISION]**
§6.1 already establishes IBKR as the source of truth for positions/cash/orders. This section extends that to everything else PaperStreet's local state touches, so backup effort goes where loss is actually costly rather than everywhere uniformly.

- **Authoritative (not backed up as data):** IBKR itself — positions, cash, and order state. Never at risk of "loss" in the backup sense; §6.1/§6.2 already require reconstructing from the broker on every startup/reconnect, so a local copy is a cache, not a record.
- **Reconstructible (backed up for convenience, not survival):** `data/futures_research.db` — fully rebuildable from the raw Databento files below via `python -m research.killed.diversified_trend.build_continuous` (see `DATA_MODEL.md`). The `market_data_bars` cache inside `paperstreet.db` is likewise re-fetchable from IBKR (subject to the 60-req/10-min pacing limit), just slow to redo, not impossible.
- **Irreplaceable (the actual backup targets):**
  - **Databento raw pulls** (`data/raw/databento/*.dbn.zst`) — a paid, point-in-time historical-data purchase; re-pulling costs money and isn't guaranteed to reproduce identically if vendor coverage changes. Protected by a manual copy to Google Drive from the Windows dev box (Kyle's process, not automated — see `OPERATIONS.md`), with integrity verified against `data/raw/MANIFEST` (vendor-issued + locally-recomputed SHA-256 per file, so a bad upload/copy is detectable).
  - **The live `paperstreet.db`** (orders, executions, account/position snapshots, strategy signals) — this is PaperStreet's own decision/fill history, not reconstructible from IBKR beyond current positions (IBKR doesn't hand back *why* an order was placed). Protected two ways: (1) on the VM, a nightly SQLite online-backup (`.backup`-equivalent, safe against a DB open in WAL mode) shipped to a small versioned S3 bucket, instance-role auth only, ~90-day retention; (2) on the Windows dev box, Kyle periodically re-uploads a fresh `sqlite3 .backup`-style snapshot to the same Google Drive archive as the Databento data (see `scripts/backup_live_db.sh` for the backup mechanism; the VM and dev-box copies are independent, unsynced databases — see the sibling note in `OPERATIONS.md`).
  - Daily whole-volume EBS snapshots (Data Lifecycle Manager, 7-day retention) cover fast whole-instance recovery after a bad deploy, layered underneath the DB-specific backup above rather than replacing it.
- **Explicitly not backed up (accepted loss):** EIA petroleum data (`research/killed/petroleum_status_drift/`) — the associated strategy is parked (see `ROADMAP.md` → Decided Against), the source (`api.eia.gov`) is free to re-pull, and the data volume is trivial.
- A backup that's never been restored isn't a backup — see `ROADMAP.md` → Backlog for the pre-live one-time restore-test gate.

---

## 7. Observability

### 7.1 Off-box structured logging **[REQUIREMENT]**
Logs are structured and shipped off the host (file + external log destination), so a dead process does not take its own death notice with it.
- **Destination [DECISION]:** A spare Raspberry Pi on the operator's home network is the destination for everything off-box — logs and the heartbeat/dead-man's switch alike (see 7.2). Chosen over a managed cloud service (Healthchecks.io, Cronitor, etc.) for cost and because the operator already has IBKR mobile/web access as an independent way to check account state. Revisit if home network/power reliability becomes a practical concern; not a permanent architectural commitment. Implemented via native `systemd-journal-upload`/`systemd-journal-remote`, not custom code.

### 7.2 Heartbeat + dead-man's switch **[REQUIREMENT]**
The engine emits a liveness signal ("alive and connected to Gateway") every N seconds. An **external** monitor alerts the operator if the heartbeat stops. This is the primary defense against silent death — the dominant year-one failure mode.
- **Monitor host [DECISION]:** The home Raspberry Pi (see 7.1), not a third-party hosted service. Known trade-off: a home power/internet outage silently takes down the monitor too, at the same time it might matter most. Accepted for now — no live strategy exists yet, so there is no current capital at risk.
- **Interval / channel [DECISION]:** ~60s heartbeat, piggybacked on the trading loop's existing bar-interval cadence, not a separate timer. Channel: **Discord**, via an incoming webhook.
- The monitor must be external to the box; a monitor on the same host that dies with the engine is worthless.
- **Schedule-aware, not naive since §9's trading-hours timers landed:** the engine is deliberately down outside 7:30am-3:30pm America/Chicago, Mon-Fri, which looks identical to a real outage to a monitor that only checks "how old is the last log line." `pi/deadmans-switch/paperstreet-deadmans-switch.sh` computes the same weekday/window (with a 5-min startup grace after 7:30 for Gateway-login + engine-reconnect latency) and only *sends* an alert when a genuine staleness is detected inside that window — state tracking (and the eventual `heartbeat_recovered`) still runs continuously across the overnight/weekend gap, so a real failure to come back up at 7:30 is still caught, not masked.

### 7.3 Alert conditions **[DECISION]**
At minimum, alert on: heartbeat loss, reconnection failure past a retry threshold, reconciliation divergence, and any order rejection the engine cannot handle. Expand as operational experience dictates. All four wired to Discord — see `alerts/discord.py`.

---

## 8. Capital Safety Interaction

Consistent with RISK.md: worst-plausible drawdown must leave equity comfortably above the $25k PDT floor. Deployment failures (silent death mid-position, failed reconciliation, missed reconnection) are capital risks equivalent to strategy risk and must be treated with the same seriousness. Drawing into PDT territory due to an operational failure is a system failure, not a P&L event.

---

## 9. Open Decisions

- **[DECISION]** Exact EC2 instance type, if traffic/load ever justifies resizing.
- **[OPEN]** 2FA for unattended reauth: mode confirmed = IBKR Mobile push (live account; see §3.3). The reauth *handling* — surviving the daily restart without a phone challenge — remains open; gated at pre-live read-only validation.
- **[DECISION]** Whether/when to containerize (§5.2).
- **[OPEN]** Home-server viability revisit criteria (deferred until post-cloud-paper). This is about the *engine* host, separate from the Pi's off-box monitoring role.
- **[DECISION]** Account mode for build/validate: production-grade infra runs against the paper account. paper↔live is an IBC `TradingMode` flag over identical code and shared (live) market-data subscriptions — paper-first is free and loses no data fidelity. Live is gated behind a discrete read-only validation of the login/2FA/reauth + real-account reconciliation path, done once infra is stable.
- **[DECISION]** Operating schedule: **market-hours-only, not continuous.** Gateway and the engine start at 7:30am and stop at 3:30pm America/Chicago (30 min before open / 30 min after the 3:00pm CT close), Mon–Fri, via `paperstreet-trading-{start,stop}.timer` (§5.1) — not IBC's `AutoLogoffTime`, which turned out not to actually stop anything once `Restart=always` was added (§3.2). Rationale: no strategy currently holds overnight/multi-day positions, so there is nothing to keep connected for; this sidesteps the daily-reauth problem (§3.2/§3.3) entirely rather than solving it. `AutoRestartTime` (the mechanism that *would* survive the reset unattended) is confirmed working, so reverting this decision is a config change, not new engineering. Revisit if/when a strategy needs to hold positions overnight — at that point §1's "host that is never touched" and §3.3's 2FA-survival concerns become live again.
