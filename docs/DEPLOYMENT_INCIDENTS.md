# DEPLOYMENT_INCIDENTS.md

What went wrong while standing up the VM+Pi paper-trading deployment (`DEPLOYMENT.md`), September 2026, and what each incident changed. Distilled from the step-by-step build log kept in the private working repo; host identifiers, account numbers, and resource IDs are omitted.

Every incident below was found on the **paper** account. No live capital was involved at any point.

---

## 1. Orders rejected at submission stayed `PENDING` forever

- **Symptom:** The first soak order was rejected with IBKR error 321 ("API interface is currently in Read-Only mode"). The `orders` row never left `PENDING`.
- **Cause:** Two things. Gateway's Read-Only API defaults to *on* for a fresh install, and IBC's `ReadOnlyApi=` left blank means "don't touch the existing setting." Separately, a rejection that happens before an order enters the book never fires `orderStatus`, so nothing ever updated the row.
- **Fix:** `ReadOnlyApi=no` in IBC's `config.ini`. `ib_app.py::error()` now marks the row `REJECTED` (and alerts) for an explicit allowlist of rejection codes, currently just 321.
- **Lesson:** The first version of the fix treated *any* non-farm-status error as a rejection. It then raced a legitimate order in the same soak run: error 399 is an advisory ("queued until market open"), not a rejection. The allowlist only grows one *observed* code at a time.

## 2. The live loop never created its database

- **Symptom:** On a fresh clone, every account snapshot, position snapshot, and bar-cache write failed with `no such table`. Each failure was caught and logged, never fatal.
- **Cause:** Every research entrypoint called `initialize_db()`. `run_live.py` didn't.
- **Fix:** `run_live.py::main()` calls it at startup (idempotent).
- **Lesson:** "Catch, log, continue" on a persistence path can mean the system runs with zero persistence and nobody notices.

## 3. One fill overwrote six unrelated order rows

- **Symptom:** When the soak's real fill arrived, `status='Filled'`, `filled_quantity=65`, and the fill price were written onto six `orders` rows. One was a stale, never-filled `SELL` from an earlier smoke test.
- **Cause:** `ib_order_id` resets to 1 on every Gateway restart, and there had been several restarts. `update_order_status_by_ib_id()` wrote `WHERE ib_order_id = ?` with no other qualifier.
- **Fix:** Correlate on `ib_perm_id` (globally unique) once known. Before that, fall back to the *newest* row with that `ib_order_id`, since a row is always inserted right before `placeOrder()`. Updates now resolve one row and write by primary key. A regression test reproduces the six-row scenario (`tests/test_database.py`).

## 4. A restart while an order was working would have doubled it

- **Symptom (found by inspection mid-soak, before it happened):** The engine's duplicate-signal suppression was in-process memory only. A restart while a `BUY` was still `PreSubmitted` would see `position == 0` and submit a second `BUY`.
- **Why it mattered:** `Restart=always` under systemd automates exactly that restart.
- **Fix:** At startup, `orders/order_handler.py::reconcile_open_orders()` asks IBKR for open orders (`reqOpenOrders`) and seeds the dedup state from any working order. `openOrder()` also self-heals the DB row's status without touching fill fields it has no data for. This blocked the systemd cutover until it landed.

## 5. No recovery from a dropped connection

- **Symptom (found by inspection):** Nothing handled `connectionClosed` or codes 1100/1102, and nothing ever called `connect()` again. Once the socket died, the trading loop's broad `except` would have retried the same doomed call every 5 seconds forever.
- **Fix:** `Session.reconnect()` rebuilds the connection from scratch, and `run_live.py::reconnect_with_backoff()` backs off 5s → 10s → 30s → 60s, generating no signals while either connection is down. Resuming before the account state is resynced is safe because the RiskGate's liveness rule already rejects orders on a stale heartbeat.
- **Validated live:** Killed Gateway's JVM mid-session. The engine backed off cleanly, evaluated zero bars during the outage, reconnected when Gateway came back, and re-read the position from a fresh broker callback.

## 6. `AutoLogoffTime` fired five hours early, and nobody noticed for six and a half hours

- **Symptom:** Gateway shut down at 11:30am ET, mid-session. The engine sat in its reconnect loop, correctly doing nothing, for ~6.5 hours until someone happened to check.
- **Cause:** IBC's `AutoLogoffTime` resolves against *Gateway's own* `jts.ini` `TimeZone`, not the OS timezone. On the fresh VM that was `Africa/Abidjan` (UTC+0), so `03:30 PM` (ported as a literal string from a Windows box running on Central time) fired at 15:30 UTC.
- **Fix:** `TimeZone=America/New_York` in `jts.ini`; `check_drift.sh` now spot-checks it. The schedule later moved to systemd entirely (incident 7).
- **Lesson:** This is the failure mode `DEPLOYMENT.md` §1 calls the most likely way to lose money in year one: a silently dead process. It was the concrete argument for building the off-box dead-man's switch next.

## 7. `Restart=always` undid the daily shutdown

- **Symptom:** The `gateway-ibc` restart counter went up by exactly one every day at 4:30pm ET. Gateway was connected nearly 24/7 while the docs said "market hours only."
- **Cause:** `AutoLogoffTime` shut Gateway down cleanly as designed. systemd can't tell a scheduled exit from a crash, so it restarted Gateway ~10s later and IBC logged straight back in.
- **Fix:** systemd now owns the schedule. `paperstreet-trading-{start,stop}.timer` issue `systemctl start`/`stop` Mon–Fri at 07:30/15:30 America/Chicago, and neither service is boot-started. A deliberate `systemctl stop` is never undone by `Restart=always`. `AutoLogoffTime` stays in `config.ini` only as a redundant backstop.
- **Follow-up:** IBC exits 143 (128+SIGTERM) on a requested shutdown, so every scheduled stop left `gateway-ibc` in `failed`. Fixed with `SuccessExitStatus=143`.

## 8. The dead-man's switch paged on scheduled downtime

- **Symptom:** A real `heartbeat_loss` alert ~3.7 minutes after the 3:30pm stop timer.
- **Cause:** The switch only checked "how old is the last engine log line." Once the engine was deliberately down outside trading hours, that would have meant alerts every weeknight, reminders every 15 minutes overnight, and all weekend. That's the alert fatigue a dead-man's switch exists to prevent.
- **Fix:** The script computes the same Mon–Fri 07:30–15:30 America/Chicago window, plus a 5-minute startup grace. It *tracks* staleness continuously but only *sends* alerts inside the window, so a failure to come back up at 07:30 still pages.
- **Caught before shipping:** In bash arithmetic, a zero-padded `HHMM` like `0800` parses as octal, and `8`/`9` aren't octal digits, so the script would have aborted at 8am and 9am. It now forces base 10 with `$((10#$hhmm))`.

## 9. The Gateway password leaks through process listings

- **Symptom:** IBC 3.24.2 always launches the JVM with the password as a literal command-line argument. `ps aux`, `pgrep -a`, and even `systemctl status gateway-ibc` (which lists the cgroup's command lines) print it. This happened more than once during the build.
- **Decision:** Accepted for the paper account (no IBC-internal workaround on this version). This must be revisited before anything touches a live account.
- **Practice:** Check Gateway with `pgrep -x`, `ss -tlnp`, `systemctl show --property=`, or `journalctl` only.

---

## Smaller gotchas

- **IBC expects the IB directory layout.** Installing Gateway straight into `~/Jts/ibgateway` fails. IBC wants `~/Jts/ibgateway/<version>/jars`.
- **Headless Gateway still needs X libraries.** Under Xvfb on a bare Ubuntu 24.04 image, the JVM also needed `libxi6 libxrender1 libxtst6 libxext6`.
- **Gateway's "stable" installer channel moves.** The VM got 10.50 while the Windows box had 10.51 a day earlier. The API surface is what matters, but it isn't pinned.
- **`systemd-journal-remote` on arm64** had a stale exact-version dependency on `libsystemd-shared`. The binary was verified against the installed library, then installed with `dpkg -i --force-depends` rather than downgrading a security-patched package. It was configured for plain HTTP since the tailnet already encrypts the transport.
- **Git Bash mangles SSM parameter names.** MSYS path conversion rewrote the leading `/` of `/paperstreet/...` into a Windows path, which surfaced as a confusing `ValidationException`. Prefix with `MSYS_NO_PATHCONV=1`.
- **Paper accounts come pre-seeded.** A 999-share QQQ position with no matching `orders` rows turned out to be IBKR's demo holding on a fresh paper account, not something the system created.
