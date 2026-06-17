# test_orch

Multi-Tenant Embedded Test Automation & Orchestration System.

This repository orchestrates embedded firmware/hardware tests across distributed
host machines, supports concurrent engineers, triggers hardware/emulation
reservations, dynamically routes jobs to the most appropriate host, and streams
live console logs back to a web interface.

> **New here?** Jump to [SETUP_AND_TESTING.md](SETUP_AND_TESTING.md) for a
> zero-to-"Get Host Status" walkthrough on a single machine.

---

## Architecture at a glance

```
        ┌──────────────┐     HTMX over WebSocket      ┌───────────────────────┐
        │   Browser    │ ◄──────────────────────────► │  Backend (FastAPI)    │
        │ (engineer/   │      HTML fragments          │  - LDAP login         │
        │  admin)      │                              │  - dispatch()         │
        └──────────────┘                              │  - /ws/logs, /ws/admin│
                                                       └───────┬───────────────┘
                                                               │
                  ┌────────────────────────────────────────────┼───────────────┐
                  │                         │                   │               │
            ┌─────▼─────┐            ┌──────▼──────┐      ┌──────▼──────┐  ┌─────▼─────┐
            │ PostgreSQL│            │  RabbitMQ   │      │  InfluxDB   │  │  Quali /  │
            │ (state)   │            │  (broker)   │      │  (logs TS)  │  │Artifactory│
            └─────▲─────┘            └──────┬──────┘      └──────▲──────┘  └───────────┘
                  │                         │ queue_<host>       │
                  │                  ┌──────▼───────┐            │
                  │  heartbeat/      │  Worker host │  log lines │
                  └──────────────────┤  (Celery)    ├────────────┘
                     state updates   │  + beat      │
                                     │  rootless    │
                                     │  Docker      │
                                     └──────────────┘
```

- **Backend** authenticates engineers (LDAP), runs the **filter-and-rank**
  scheduler, dispatches jobs to a specific host's queue, and pushes live updates
  to the browser as HTML fragments over WebSockets.
- **Worker hosts** each consume only their own `queue_<host_id>`, run tests in
  rootless Docker containers, stream logs, upload artifacts, and publish periodic
  heartbeats. Beat also reaps stale runs/reservations.
- **PostgreSQL** is the central state store (users, sessions, host inventory,
  executions). Using a networked DB instead of SQLite is what enables the
  multi-host topology.
- **RabbitMQ** carries dispatch tasks and the fanout exchanges used for live log
  streaming and the admin host-status poll.
- **InfluxDB** holds the full time-series log history.

## Repository layout

```
shared/      Cross-service contracts + validation (schemas, host_stats, validation)
backend/     FastAPI app, scheduler, services, templates, Dockerfile
  auth/      LDAP authentication + session resolution
  services/  scheduler, influx_client, quali_client, status_render
  templates/ HTMX-driven HTML (base, login, dashboard, admin)
worker/      Celery app + tasks, rootless Docker engine, runners, Dockerfile
tests/       Pytest unit suite (pure logic; no broker/DB needed)
```

## Quick start

```bash
cp .env.example .env
make up            # or: docker compose up --build
```

Then follow [SETUP_AND_TESTING.md](SETUP_AND_TESTING.md) to log in and press
**Get Host Status**.

## Key design decisions

- **One queue per host** (`queue_<host_id>`) gives the scheduler precise control
  over placement; the worker starts with `--queues=queue_<host_id>`.
- **Filter-and-rank** placement: drop ineligible hosts (offline, full, missing
  hardware tags, no emulation), then pick the lightest-loaded survivor. Dispatch
  locks rows `FOR UPDATE` and reserves a slot atomically.
- **Capacity is optimistic + reconciled.** Dispatch increments
  `active_containers`; the worker decrements it after container removal; the 30s
  heartbeat then sets the absolute live count as the source of truth.
- **Boundary validation vs. invariants.** `require_*` helpers raise at trust
  boundaries (broker payloads, LDAP/env input) and run even under `python -O`;
  plain `assert`s guard internal "can't happen" conditions.
- **Live UI without a SPA.** HTMX + the WebSocket extension swap server-rendered
  HTML fragments via out-of-band targets — no custom front-end framework.

## Testing

```bash
make test          # pytest
make lint          # ruff
```

The unit suite covers pure logic (validation, scheduler scoring/filtering,
host-stats clamping, status rendering, schema round-trips) and does not require
the broker, database, or Docker.

## Notes & caveats

- `init_db()` is dev convenience, not migrations. Use a real migration step
  (e.g. Alembic) before production.
- `active_users` reads `0` inside containers (no host utmp); bind-mount
  `/var/run/utmp` or run the worker on the host for real numbers.
- The bundled worker colocates Celery `worker` + `beat` for simplicity; split
  them in production.