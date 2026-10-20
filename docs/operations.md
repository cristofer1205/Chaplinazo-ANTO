# Operate Antonella without losing acknowledged sales

**Launch is blocked until a human independently approves every map assignment.**
The supplied map is an agent source transcription, not an approved seating plan.
The application deliberately refuses to seed or start with its current `pending`
approval. No production deployment, tunnel configuration, or host change has been performed.

## Quick path: validate locally

Run from the repository root, with Python 3.12 or newer:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pytest tests/test_unit.py tests/test_integration.py tests/test_operations.py -q
# Chromium is required; use CHROMIUM_PATH for a system browser or install
# Playwright's matching browser with: .venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest tests/test_e2e.py -q
docker compose -p antonella-apply-isolated -f compose.yaml build
RUN_COMPOSE=1 .venv/bin/python -m pytest tests/test_compose.py -q -s
```

The tests use **synthetic approved fixtures only**. The Compose test creates a
unique `antonella-smoke-*` project, random loopback port, separate network and
disposable volume. Its `finally` block removes only that test project and volume.
It tests the real unapproved image's refusal before mounting the synthetic map.
Do not confuse these proofs with approved-map or production readiness.

## Approve the map before seeding

1. Compare each of the 69 records in `data/venue.json` with the source image:
   printed ID, capacity, level boundary, and label-center position. The coordinate
   system is the original 1600 × 1236 image, not physical dimensions. Review the
   side tables carefully; levels wrap around the central blocks.
2. Verify the original image's SHA-256 is
   `c469e87017a8f662fd98fd432a426af95d83aca6276e11d0947db7c46a7d1add`.
   Fix any uncertain entry, mark unresolved entries `verified: false`, and keep
   approval pending. Never infer levels from numerical ranges or the total alone.
3. Only after the independent human review, record that person's actual name,
   timezone-aware `approved_at` timestamp and explicit `status: approved` in
   `verification`. Compute `map_sha256` with `app.db.map_hash(data['tables'])`.
   This hash covers every assignment and verification flag. Keep the actual
   review evidence with the change; setting a string is not identity proof.
4. Rebuild the image and validate the gate. Seed is transactional and non-resetting:
   changed map/schema against an existing volume is rejected, never overwritten.

No command in this guide constitutes approval, deployment permission, or a
request to alter the existing tunnel. Real-map approval remains an external gate.

## Runtime boundaries

| Boundary | Policy |
|---|---|
| Network | Dedicated, non-external Compose bridge; no shared service network or Docker socket. Only `127.0.0.1:8000` is published; SQLite has no TCP listener. |
| Egress | Not denied. Docker 29 suppressed loopback publication on an internal-only bridge, so this uses an ordinary project-scoped bridge instead. |
| Container | UID/GID 10001, read-only root, dropped capabilities, writable private data volume and temporary filesystem only. |
| Durability | WAL, FULL synchronization, per-request connections, 250 ms lock wait, one atomic transaction per change. |
| Availability | `unless-stopped` restarts the container. Actual host-boot recovery is a separate untested launch gate. |
| Public editing | No authentication. Anyone can correct anyone; names are unverified labels. Do not treat the history as proof of identity. |
| Abuse | Global 30 write attempts/minute, including invalid submissions. One worker owns the limiter; it resets on restart and is not DDoS protection. Do not increase worker count without a shared limiter. |
| Privacy | No buyer fields, request-body logging, or access logs. Free text can still contain unwanted PII: warnings are not prevention. Restrict volume/backup access. |
| Browser | Poll every 5 s; disable stale/offline writes after 15 s. Preserve drafts, require review after conflicts or unconfirmed responses, never automatically replay. |

The existing tunnel must eventually forward to the loopback origin with the
allowed public host. Do not enable proxy-header trust to accept arbitrary hosts.
Only the exact public HTTPS origin and documented local port 8000 origins are
allowed for browser writes. Nonbrowser callers may omit Origin; this is not auth.

## Backup ownership and retention

| Responsibility | Required operating policy |
|---|---|
| Owner | Event operator; a named primary and deputy must be appointed before launch (not assigned yet). |
| Schedule | Daily and immediately before upgrades or planned maintenance. Scheduling is not installed by this change. |
| Local destination | A protected operator-owned directory outside the repository and app volume; choose the actual path before launch. |
| Off-host destination | Encrypted storage outside this PC, with access limited to the appointed operators; destination is not assigned yet. |
| Retention | Keep 30 daily snapshots and 8 weekly snapshots; keep pre-upgrade snapshots until the upgrade and restore drill are accepted. Do not prune the last verified copy. |
| Verification | Every snapshot must pass integrity, foreign keys, approved-map hash, complete audit-chain and current-count checks. Perform a separate restore drill regularly. |

Use SQLite's backup API, **never copy the live `.sqlite`, `-wal` or `-shm` files**.
The tool creates private files without overwriting existing destinations and
verifies them before reporting success. Run from the repository root:

```bash
.venv/bin/python -m tools.backup snapshot /absolute/live.sqlite /absolute/protected/snapshot.sqlite
.venv/bin/python -m tools.backup verify /absolute/protected/snapshot.sqlite
```

For Compose, the same tool is available in the image. First create a uniquely
named snapshot inside the private volume, then export that *completed standalone
snapshot*, not the live database:

```bash
docker compose -p antonella -f compose.yaml exec -T app python -m tools.backup snapshot /data/sales.sqlite /data/snapshot-YYYYMMDD.sqlite
docker compose -p antonella -f compose.yaml cp app:/data/snapshot-YYYYMMDD.sqlite /absolute/protected/snapshot-YYYYMMDD.sqlite
```

Protect the exported file's permissions, verify it again, and copy it off-host.
Destination names must be unique; the backup tool refuses to replace any file.

## Restore only while stopped

1. Stop **all** writers. For Compose, use `docker compose -p antonella -f
   compose.yaml stop app`. Preserve the entire old volume for rollback. Never
   run `down --volumes` on the real project and never delete live sidecars.
2. Verify the selected snapshot. Ensure UID 10001 can read the recovery snapshot
   if it is mounted into a one-off container; do not make it world-readable.
3. Restore to a **new, absent** path on the data volume. The tool rejects an
   existing target or stale target sidecars. `--stopped` records an operator
   assertion, not automatic detection that no other process is running.

```bash
# Example for a snapshot already retained inside the private volume:
docker compose -p antonella -f compose.yaml run --rm --no-deps --entrypoint python app -m tools.backup restore /data/snapshot-YYYYMMDD.sqlite /data/restored-YYYYMMDD.sqlite --stopped
docker compose -p antonella -f compose.yaml run --rm --no-deps --entrypoint python app -m tools.backup verify /data/restored-YYYYMMDD.sqlite
```

4. Use an operator-controlled Compose override setting `services.app.environment.DATABASE`
   to `/data/restored-YYYYMMDD.sqlite`. Reopen the app with that override and
   confirm all 69 map records, sold counts, versions and complete paginated
   history match the chosen snapshot. Restart once more and compare again.
5. Reopen routing only after comparison succeeds. If anything differs, stop,
   preserve both databases and investigate; do not attempt ad hoc count edits.

Restoration intentionally rolls back sales made after the snapshot. Account for
those changes operationally; never silently present an older snapshot as current.

## Incident rollback and launch checklist

For abuse, disable writes with `WRITES_ENABLED=0` and recreate only this service
after operator authorization. Preserve the volume/history, investigate, then
append audited corrections. Database owners can bypass SQL triggers; the audit
is application-immutable, not cryptographically tamper-proof.

- [ ] Independent map approval and source/hash evidence recorded.
- [ ] Named backup operators, actual protected/off-host destinations and schedule assigned.
- [ ] Real approved volume restart and stopped-restore drill accepted.
- [ ] HTTPS at `antonella.wara-api.xyz` and tunnel reachability checked after separate deployment authorization.
- [ ] Host-boot recovery and Docker startup policy tested with explicit host authorization.
- [ ] SQLite privacy and only loopback app publication rechecked on the target deployment.
- [ ] Intercorp/Intertel baseline and post-launch checks completed without modifying them.

**HTTPS, tunnel, neighboring-service and host-reboot checks have not been run.**
Isolated container restart/restore tests do not replace these launch gates.
