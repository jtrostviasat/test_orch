# Setup & Testing Guide

This guide walks you from an empty machine to clicking **Get Host Status** in the
admin panel and seeing a live runner row appear.

## What you'll run

For this test you need the full central stack **plus one worker**. The good news:
`docker compose up` starts **all of it on one machine** (Postgres, RabbitMQ,
InfluxDB, backend, and `worker_host_01`). You do **not** need a second physical
host to test the button — the bundled worker is a real runner that will respond.

---

## Prerequisites

| Requirement | Check |
| --- | --- |
| **Docker Engine** (rootless recommended, see note) | `docker version` |
| **Docker Compose v2** | `docker compose version` |
| All project files in the repo root | `ls` shows `docker-compose.yml`, `backend/`, `worker/`, `shared/` |

> **Rootless note:** The spec targets Docker Rootless. The "Get Host Status"
> feature itself does **not** require the Docker socket — it only reads
> CPU/RAM/disk/users via `psutil`. So you can test the button even with regular
> Docker. The socket only matters when you actually *run a test container*. The
> rootless-specific step is flagged where it applies.

---

## Step 1 — Create your `.env`

```bash
cp .env.example .env
```

Open `.env` and confirm these (defaults work for local single-host):

```bash
WORKER_HOST_ID=worker_host_01

POSTGRES_HOST=postgres
POSTGRES_USER=test_orch
POSTGRES_PASSWORD=test_orch
POSTGRES_DB=test_orch

CELERY_BROKER_URL=amqp://guest:guest@rabbitmq:5672//
RABBITMQ_USER=guest
RABBITMQ_PASSWORD=guest

INFLUX_URL=http://influxdb:8086
INFLUX_TOKEN=changeme-influx-token
INFLUX_ORG=test_orch
INFLUX_BUCKET=test_logs

HOST_POLL_GRACE_SECONDS=4.0
```

### Rootless-only: point at your rootless socket

Find your UID and set the socket path:

```bash
id -u                       # e.g. prints 1000
echo $XDG_RUNTIME_DIR       # e.g. /run/user/1000
```

Then in `.env`:

```bash
DOCKER_HOST=unix:///run/user/1000/docker.sock
DOCKER_HOST_SOCK=/run/user/1000/docker.sock
```

If your UID is **not** 1000, you must also change the `--uid 1000` in
`worker/Dockerfile` to match, or the socket mount won't be usable. (Again: not
needed just to test the status button.)

---

## Step 2 — Build and start the stack

```bash
docker compose up --build
```

Watch the logs. Wait until all five services are healthy/ready:

- `postgres` -> `database system is ready to accept connections`
- `rabbitmq` -> `Server startup complete`
- `influxdb` -> `msg=Listening ... service=tcp-listener`
- `backend` -> `Uvicorn running on http://0.0.0.0:8000`
- `worker` -> `celery@... ready.`

The `depends_on` health checks make backend/worker wait for the infra
automatically. First build takes a few minutes.

> Keep this terminal open to watch logs, or add `-d` to run detached.

---

## Step 3 — Confirm the worker registered a heartbeat

The button polls hosts that exist in the DB. The worker's **beat** publishes a
heartbeat every 30s, which **creates its `WorkerHost` row** on first run. Give it
~30 seconds after the worker logs `ready`, then verify:

```bash
docker compose exec postgres \
  psql -U test_orch -d test_orch -c \
  "SELECT host_id, is_online, active_containers, cpu_utilization, last_heartbeat FROM worker_hosts;"
```

You should see one row:

```
    host_id     | is_online | active_containers | cpu_utilization |       last_heartbeat
----------------+-----------+-------------------+-----------------+----------------------------
 worker_host_01 | t         |                 0 |             3.5 | 2026-06-17 ...
```

If the row is there, the worker is alive and will answer the poll.

**If there's no row after a minute**, check the worker logs for the heartbeat task:

```bash
docker compose logs worker | grep -i heartbeat
```

If you don't see `publish_heartbeat` running, the beat scheduler isn't up —
confirm the worker command includes `--beat` (it does in the provided
`worker/Dockerfile`).

---

## Step 4 — Log in to the web UI

The admin page requires a session, and login goes through **LDAP**. For a local
test without a real directory you have two options.

### Option A (recommended): seed a session directly — no LDAP needed

```bash
docker compose exec postgres psql -U test_orch -d test_orch <<'SQL'
INSERT INTO users (username, display_name, email, created_at)
VALUES ('tester', 'Local Tester', 'tester@example.com', now())
ON CONFLICT (username) DO NOTHING;

INSERT INTO user_sessions (token, user_id, created_at, expires_at)
SELECT 'LOCALTESTTOKEN', id, now(), now() + interval '12 hours'
FROM users WHERE username = 'tester'
ON CONFLICT (token) DO NOTHING;
SQL
```

Then set the cookie in your browser:

1. Open `http://localhost:8000/admin` (you'll be redirected to `/login`).
2. Open DevTools -> **Application/Storage -> Cookies -> http://localhost:8000**.
3. Add a cookie named `session_token` with value `LOCALTESTTOKEN`.
4. Reload `http://localhost:8000/admin`.

### Option B: stand up a test LDAP

Run a test directory (e.g., an `osixia/openldap` container) and log in via the
form. Heavier; only do this if you specifically want to exercise the LDAP path.

---

## Step 5 — Press "Get Host Status"

On `http://localhost:8000/admin`:

1. You'll see the **Worker Host Inventory** table (last-known heartbeat values)
   with `worker_host_01`.
2. Below it, the **Live Host Status** section with the **Get Host Status** button.
3. Click it.

**What should happen:**

- The results table immediately clears to "Waiting for 1 runner(s) to respond...".
- Within a second, a row appears for `worker_host_01` showing:

| Host ID | CPU % | RAM avail (MB) | Disk avail (GB) | Active Users |
| --- | --- | --- | --- | --- |
| worker_host_01 | 4.2 | 1850.5 / 4096 | 45.30 / 100 | 0 |

- After ~4s (`HOST_POLL_GRACE_SECONDS`), the green sentinel appears:
  **Polling complete — all 1 runner(s) responded.**

That's the full request -> fan-out -> worker `psutil` collection -> RabbitMQ reply
-> WebSocket push -> live render path working end-to-end.

> **Expect `Active Users = 0`.** The worker runs in a container and can't read
> the host's utmp, so logged-in users read as 0. CPU/RAM/disk reflect the worker
> container's view. This is documented MVP behavior — not a bug. For real
> host-level numbers, bind-mount `/var/run/utmp` or run the worker directly on the
> host.

---

## Step 6 (optional) — See the "partial response" sentinel

To watch the amber "N of M" path, register a **second** host that won't answer:

```bash
docker compose exec postgres psql -U test_orch -d test_orch -c \
  "INSERT INTO worker_hosts (host_id, is_online, max_containers) VALUES ('worker_host_02', true, 10);"
```

Click **Get Host Status** again. Now:

- `worker_host_01` responds (one row appears).
- `worker_host_02` has no worker consuming `queue_worker_host_02`, so it never
  replies.
- After 4s: **Polling complete — 1 of 2 runner(s) responded (1 did not respond
  in time).**

This demonstrates the "for each runner that responds" behavior — dead/absent hosts
simply don't show up, and the sentinel quantifies it.

Clean up the fake host afterward:

```bash
docker compose exec postgres psql -U test_orch -d test_orch -c \
  "DELETE FROM worker_hosts WHERE host_id = 'worker_host_02';"
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `/admin` keeps redirecting to `/login` | No valid `session_token` cookie | Re-do Step 4; confirm token not expired |
| Button does nothing, no "Waiting..." text | WebSocket didn't connect | DevTools -> Network -> WS: confirm `/ws/admin` is `101 Switching Protocols`; check backend logs |
| "Waiting..." but no row ever appears | Worker not consuming its queue, or RabbitMQ unreachable | `docker compose logs worker`; verify `--queues=queue_worker_host_01`; check `rabbitmq` is healthy |
| No `worker_host_01` row in DB | Beat not running / <30s elapsed | Wait 30s; `docker compose logs worker \| grep heartbeat` |
| Sentinel says "0 of 1" | Worker received task but `_publish_stats_reply` failed | Check worker logs for AMQP publish errors; confirm `RABBITMQ_*` creds match between services |
| `psycopg`/DB connection errors in backend | Postgres not ready or wrong creds | Ensure `POSTGRES_*` in `.env` match the `postgres` service; wait for healthcheck |

Quick health peek at the queue (optional): open the RabbitMQ UI at
**http://localhost:15672** (guest/guest) -> Queues tab -> you should see
`queue_worker_host_01` with a consumer.

---

## Verifying the resiliency fixes

- **Container-count decrement:** dispatch a real test bundle, and while it runs
  check `active_containers` is `1`, then after it finishes confirm it returns
  toward `0` *before* the next 30s heartbeat — rather than only correcting on the
  heartbeat.
- **Re-poll guard:** rapidly click **Get Host Status** several times. Each click
  should fully reset the table; you should never see rows from a previous click
  bleed into a new poll's results, and you should get exactly one sentinel per
  click.

---

## Adding a real second worker host (beyond local testing)

The single bundled worker is `worker_host_01`. To run a genuine second runner on
another machine:

1. Copy the repo to the second host.
2. Set a **unique** `WORKER_HOST_ID` (e.g., `worker_host_02`) in its `.env`.
3. Point its `CELERY_BROKER_URL`, `POSTGRES_*`/`DATABASE_URL`, and `INFLUX_URL`
   at the **central** services on the first host (use reachable hostnames/IPs,
   not `localhost`).
4. Start only the worker on that host (build the `worker/` image and run it, or
   use a worker-only compose file).

Each worker consumes its own `queue_<WORKER_HOST_ID>`; the backend's
filter-and-rank scheduler dispatches to the right queue automatically, and the
admin poll will list every host that responds.

---

## MVP: run a test and watch its logs stream live

This exercises the full path: submit → filter-and-rank dispatch → container run →
stdout streamed to InfluxDB + a RabbitMQ fanout → WebSocket → live terminal.

### 1. Build the demo test image (on the worker host)

The worker launches a container from the image you name, so it must exist in the
host's rootless Docker daemon. A throwaway "test" image is provided that prints a
random INFO log line to stdout (and to `/artifacts/run.log`) every 10s for ~1 min:

```bash
./examples/mvp-test/build.sh        # builds mvp-test:latest
```

### 2. Log in

Use the cookie shortcut (`./setup_admin_user.sh`, then set the `session_token`
cookie) or a real LDAP login. See Step 4 above.

### 3. Submit a test

On the **Dashboard** ("Submit a Test" panel):

- **Runner:** `pytest` (the demo image ignores the rendered config)
- **Framework image:** `mvp-test:latest`
- Leave tags/emulation empty.
- Click **Dispatch Test**.

The scheduler picks the least-loaded qualifying host (your `worker_host_01`),
reserves a slot, and enqueues the run. A new row appears in **My Test Runs** with
status `DISPATCHED` → `RUNNING`.

### 4. Watch the logs

Click the **test id** in the runs table. The log page opens a WebSocket to
`/ws/logs/<test_id>` and appends each line as it arrives — you'll see a new line
roughly every 10 seconds, finishing with `=== MVP test complete: PASSED ===`
after ~1 minute. The run then flips to `PASSED` (refresh the dashboard).

> The streamed lines come from the container's stdout. The same lines are also
> written to InfluxDB (permanent history) and to `/artifacts/run.log` inside the
> per-test artifacts mount on the worker
> (`<TEST_RUNS_DIR>/<test_id>/artifacts/run.log`). Artifact upload to Artifactory
> is attempted only if a real Artifactory is configured; without one it is logged
> and skipped — the run still passes.

### Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Run goes straight to `FAILED` | Image not found / no host qualified | Confirm `mvp-test:latest` is built on the host; confirm a `worker_host_01` row exists and is online |
| Log page shows nothing | WebSocket/broker issue, or not the run's owner | DevTools → Network → WS: confirm `/ws/logs/...` is `101`; the viewer must be the user who submitted the run |
| `4401`/`4403` on the log socket | Not logged in / not the owner | Re-do login; only the submitting user can view a run's logs |
