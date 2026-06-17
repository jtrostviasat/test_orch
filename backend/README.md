# backend

FastAPI application: authentication, the scheduler/dispatch entry point, service
clients, and the live WebSocket channels that push HTML fragments to the browser.

## Layout

| Path | Purpose |
| --- | --- |
| `config.py` | Env-driven `Settings` (pydantic-settings); shared with the worker. |
| `database.py` | SQLAlchemy 2.x engine/session (psycopg 3), `init_db`, `get_db`. |
| `models.py` | ORM models: User, UserSession, WorkerHost, TestExecution, LogLine. |
| `auth/` | LDAP bind + session resolution. |
| `services/scheduler.py` | Filter-and-rank placement + atomic `dispatch`. |
| `services/influx_client.py` | Write/read log lines to/from InfluxDB. |
| `services/quali_client.py` | Release Quali CloudShell reservations. |
| `services/status_render.py` | Pure HTML-fragment renderers for the admin poll. |
| `templates/` | HTMX-driven pages (`base`, `login`, `dashboard`, `admin`). |
| `main.py` | Routes, `/ws/logs/{test_id}`, and `/ws/admin`. |

## The live channels

### `/ws/logs/{test_id}`
Binds a temporary queue to the test's fanout exchange (`test_logs.<test_id>`) and
streams each log line to the browser as an out-of-band append into `#terminal`.
The consumer task is always cancelled on disconnect, and the AMQP connection is
closed in a `finally` so cancellation can't leak it.

### `/ws/admin` — "Get Host Status"
On each inbound frame the server:

1. Mints a fresh **correlation id** and bumps a **generation** counter.
2. Clears the results table and sentinel (out-of-band `innerHTML`).
3. Fans `report_status` out to every host's `queue_<host_id>`.
4. Streams each `HostStats` reply back as a row append as it arrives.
5. After `HOST_POLL_GRACE_SECONDS`, emits a soft sentinel:
   *"all responded"* (green) or *"N of M responded"* (amber).

**Re-poll safety:** only the current generation may render. A late reply from a
superseded poll is suppressed, so it can never cross-render into a freshly
cleared table. The sentinel is latched inside a lock so the grace timer and a
straggling final reply can't double-emit.

## Scheduler

`dispatch()` is authoritative: it locks candidate `WorkerHost` rows `FOR UPDATE`,
filters out ineligible hosts, ranks survivors by `active_containers * 20 + cpu`,
reserves a slot (`active_containers += 1`), commits, then `apply_async`s the task
to the chosen host's queue. If the broker publish fails, the reservation is
rolled back so capacity isn't leaked until the next heartbeat.