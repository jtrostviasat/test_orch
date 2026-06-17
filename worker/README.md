# worker

A single distributed worker host. Runs the Celery `worker` (consuming only its
own `queue_<host_id>`) plus `beat` for periodic maintenance.

## Layout

| Path | Purpose |
| --- | --- |
| `celery_app.py` | Celery app, queue routing, and the beat schedule. |
| `tasks.py` | `run_test_bundle`, `report_status`, `publish_heartbeat`, `reap_stale`. |
| `docker_engine.py` | Rootless Docker control; only counts/removes `test_`-prefixed containers. |
| `translation_layer.py` | UI variables → framework config (pytest/robot runners). |
| `artifact_uploader.py` | PUT result files to Artifactory. |

## Tasks

- **`run_test_bundle`** — translate config → start container → stream logs to
  InfluxDB + the live fanout → upload artifacts → persist final status. Always
  removes the container and releases the optimistic slot in `finally`.
- **`report_status`** — answer one admin poll for this host by publishing a
  `HostStats` snapshot to `host_status.<correlation_id>`.
- **`publish_heartbeat`** (every 30s) — upsert this host's `WorkerHost` row;
  sets `active_containers` to the **absolute** live container count (the
  reconciling source of truth) and marks the host online. First run is how a host
  **registers** itself.
- **`reap_stale`** (every 120s) — fail runs stuck `RUNNING` past the deadline and
  release their Quali reservations.

## Capacity accounting (why it's correct)

```
dispatch:           active_containers += 1     (optimistic reservation)
run_test_bundle:    ... run ...
  finally:          cleanup(container)         (remove first)
                    active_containers -= 1     (then release, clamped ≥ 0)
heartbeat (30s):    active_containers = live   (absolute reconcile)
```

Removing the container **before** decrementing means a heartbeat racing in
between still observes an accurate live count, so the gauge never drifts upward.

## Rootless Docker

The worker talks to the user's rootless Docker socket (`DOCKER_HOST`). The socket
is bind-mounted in `docker-compose.yml`, and the image's `--uid` **must** match
the host UID that owns the socket. If your UID isn't `1000`, update both the
`useradd --uid` in `worker/Dockerfile` and the socket path in `.env` /
`docker-compose.yml`.

## Adding a framework

Subclass `Runner` in `translation_layer.py`, implement `write_config()`, and
register it in the `_RUNNERS` map. No other code changes are required.