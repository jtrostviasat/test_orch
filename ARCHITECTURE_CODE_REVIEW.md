# Full Code Review — test_orch vs. Original Specification

Reviewed against the "Multi-Tenant Embedded Test Automation & Orchestration System"
spec. Note: PostgreSQL replacing SQLite is an intentional, well-executed change
(see `config.py`/`database.py`) and is treated as correct, not a deviation.

**Overall:** The pure/leaf code (validation, schemas, scheduler ranking, status
rendering, host stats) is genuinely high quality — well-documented, validated at
trust boundaries, and unit-tested. The weaknesses are all at the **integration
seams**: the wiring between backend, worker, and the spec's end-to-end flows.

---

## 🔴 CRITICAL — Will break at runtime or defeat a core requirement

### C1. Backend image cannot import `worker` — "Get Host Status" will crash
- **Where:** `backend/main.py:487` (`from worker.tasks import report_status`) and
  `backend/services/scheduler.py:162` (`from worker.tasks import run_test_bundle`),
  vs. `backend/Dockerfile` which only does `COPY shared` and `COPY backend`.
- **Problem:** The `worker/` package is **not copied into the backend image**. Both
  imports are lazy (inside functions), so the backend boots fine — but the moment an
  admin clicks **Get Host Status**, `ws_admin` executes the import and raises
  `ModuleNotFoundError: No module named 'worker'`. The same applies to any future
  dispatch route.
- **Why it matters:** This is the exact feature `SETUP_AND_TESTING.md` is built
  around. It has likely not been exercised end-to-end yet.
- **Fix (preferred):** Don't import worker code in the backend at all. Send tasks by
  name via Celery, which needs no worker module:
  ```python
  from worker.celery_app import celery_app  # or a backend-local Celery() handle
  celery_app.send_task("worker.report_status", args=[correlation_id],
                       queue=queue_name_for(host_id))
  celery_app.send_task("worker.run_test_bundle", args=[bundle.to_dict()],
                       queue=target_queue)
  ```
  This decouples backend from worker internals entirely. Alternatively, `COPY worker`
  into the backend image — but that drags Docker SDK/psutil concerns into the web tier
  and is the weaker option.

### C2. No entry point to submit a test — the core workflow is unreachable
- **Where:** `backend/main.py` exposes only `/`, `/login`, `/admin`,
  `/ws/logs/{test_id}`, `/ws/admin`. `scheduler.dispatch()` and `select_host()` are
  never called by any route (confirmed: only referenced in tests).
- **Problem:** Spec §1/§3 describe engineers bundling tests → filter-and-rank →
  dispatch → live logs. There is **no API/UI path that creates a `TestExecution` or
  calls `dispatch()`**. The dashboard only lists rows that must be inserted manually
  into Postgres. The whole "bundle and run a test" path exists as library code with no
  caller.
- **Fix:** Add `POST /tests` (and a dashboard form) that builds a `TestBundleRequest`,
  persists a `TestExecution(status=PENDING)`, calls `dispatch(db, bundle)`, and
  records the chosen host / `DISPATCHED`. This also makes `/ws/logs/{test_id}`
  reachable in practice.

### C3. Dead-host detection is missing — defeats spec §D resiliency
- **Where:** `worker/tasks.py:publish_heartbeat` sets `host.is_online = True`;
  **nothing anywhere sets `is_online = False`** (confirmed by grep).
- **Problem:** When a worker crashes, its row stays `is_online=True` with its last
  `active_containers`/`cpu_utilization` frozen. The scheduler (`_passes_filter`) will
  keep ranking and routing jobs to a machine that is gone. Spec §D explicitly requires
  catching the missing-heartbeat state.
- **Fix:** Add a central sweep (backend-side beat or a host-independent task) that marks
  `is_online = False` where `last_heartbeat < now - N*heartbeat_interval`, and have the
  scheduler additionally treat a stale `last_heartbeat` as offline even before the sweep
  runs.

### C4. Crash-cleanup depends on the crashed host running it
- **Where:** `worker/tasks.py:reap_stale` filters
  `TestExecution.target_host_id == settings.worker_host_id`.
- **Problem:** Each worker only reaps **its own** stuck runs. If that host is dead, its
  beat isn't running, so its stuck `RUNNING` rows are never failed and its Quali
  reservations are never released — precisely the scenario §D is meant to handle. The
  reaper only helps for transient per-task hangs on a *live* host.
- **Fix:** Move stale-run reaping to a **central** scheduler (backend beat) keyed off
  missing heartbeats (ties into C3), independent of any single worker being alive.

---

## 🟠 HIGH — Correctness / security gaps

### H1. `/ws/logs/{test_id}` has no authentication
- **Where:** `backend/main.py:271`. Unlike `/ws/admin` (which calls `resolve_session`),
  the log socket accepts any client and streams logs for any guessable `test_id`.
- **Fix:** Resolve the session cookie and close with `4401` if unauthenticated; ideally
  also verify the user owns (or is admin for) that `test_id`.

### H2. `login_submit` error path passes a fake request object
- **Where:** `backend/main.py:194` — `{"request": {}}` on the failure branch.
- **Problem:** Starlette templates expect a real `Request`; this only "works" because
  `base.html` happens not to use `request`. It's fragile and will break the moment a
  template references `request`/`url_for`.
- **Fix:** Thread the real `Request` into `login_submit` and pass it through.

### H3. Auto-registered hosts can never run tagged/emulation jobs
- **Where:** `publish_heartbeat` creates a host with defaults
  (`max_containers=10`, `hardware_tags=""`, `supports_emulation=False`) and there is no
  admin path to edit these.
- **Problem:** Any bundle with `required_hw_tags` or `requires_emulation=True` will
  filter out every auto-registered host forever (`_passes_filter`). The ranking engine
  is effectively limited to untagged jobs.
- **Fix:** Provide a way to set host capabilities (admin form/endpoint, seed file, or
  worker-reported tags from env/config at heartbeat time).

### H4. Flux query interpolates `test_id` directly
- **Where:** `backend/services/influx_client.py:read_lines` builds the Flux string with
  f-string interpolation of `test_id`.
- **Problem:** `require_non_empty_str` validates presence/type but not content. If a
  `test_id` ever becomes user-influenced, this is a Flux-injection vector.
- **Fix:** Constrain `test_id` to a strict charset (e.g. `^[A-Za-z0-9_-]+$`) at the
  boundary, or use parameterized Flux (`query_params`).

---

## 🟡 MEDIUM — Robustness / spec alignment

### M1. `network_mode="bridge"` contradicts spec §C
- `worker/docker_engine.py:71` uses `bridge`; spec asks for `host` networking or explicit
  corporate DNS so the container can reach the Quali API. Confirm reachability or make it
  configurable.

### M2. `dispatch()` re-implements ranking instead of reusing `select_host()`
- `scheduler.py:159` duplicates the `min(..., key=(_workload_score, host_id))` logic.
  Extract a shared `_rank(survivors)` helper so the locked and read-only paths can't
  drift apart.

### M3. `quali_reservation_id` is never populated on `TestExecution`
- The model and reaper/except paths reference it, but nothing sets it (because there's no
  dispatch route — see C2). When C2 is implemented, ensure the reservation id is recorded
  so `reap_stale`/the failure path can actually release it.

### M4. Per-line synchronous writes to Postgres **and** InfluxDB
- `run_test_bundle` writes every log line to InfluxDB and inserts a `LogLine` row
  (batched every 25). For chatty tests this is heavy double-writing on the hot path.
  Consider InfluxDB as the sole high-volume sink and only index sparse checkpoints in
  Postgres.

### M5. `.env.example` is missing
- `SETUP_AND_TESTING.md` step 1 says `cp .env.example .env`, but the file doesn't exist
  in the repo. Add it (mirroring the `Settings` fields) so setup matches the docs.

### M6. `@app.on_event("startup")` is deprecated
- `backend/main.py:93` — modern FastAPI prefers the lifespan context manager. Works today;
  will warn/break on a future upgrade.

---

## 🔵 LOW — Polish

- **L1.** Session cookie lacks `secure=True` (`main.py:198`); fine for local, set it for
  prod/TLS.
- **L2.** `secret_key`/token defaults are `"change-me"`/`"changeme-*"`; ensure prod
  overrides via env (and consider failing fast if left default when `app_env=production`).
- **L3.** `MEASUREMENT = "test_log"` (singular) vs. the `test_logs` bucket naming in the
  spec — harmless but slightly confusing.
- **L4.** Two separate `_escape` helpers (`main.py` and `status_render.py`); consolidate
  into `shared/`.
- **L5.** Test coverage is solid on pure functions but absent on `dispatch` (the locking
  path), `tasks`, `auth`, `influx_client`, and the websocket flows — the areas where the
  critical bugs above actually live.

---

## What's genuinely good (keep it)
- Trust-boundary validation discipline (`require_*` that survive `python -O`, `assert`
  reserved for internal invariants) — well thought out and consistently applied.
- `TestBundleRequest` validates on construction, so malformed jobs can't reach the broker.
- The `/ws/admin` re-poll design (generation tokens + idempotent sentinel latching under a
  lock) is careful concurrency work and reads correctly.
- Optimistic slot reservation with rollback-on-dispatch-failure in `dispatch()` is the
  right pattern (it just needs a caller).
- Postgres migration (pooling, `pool_pre_ping`, discrete-or-URL config) is clean.

---

## Suggested priority order
1. **C1** (decouple backend→worker via `send_task`) — unblocks the demo feature.
2. **C3 + C4** (central heartbeat-based offline marking & reaping) — delivers §D.
3. **C2** (test-submission route) — makes the core workflow real.
4. **H1, H2, H3, H4** — security/correctness.
5. **M-series**, then **L-series**.
