# Worker Code Review

## Summary
Comprehensive review of worker scripts for correctness, error handling, and operational safety. Overall solid foundation with several areas for hardening.

---

## 1. **docker_engine.py** ✅ Good

### Strengths
- Clean separation of concerns (container lifecycle, log streaming, cleanup)
- Proper validation of inputs via `require_non_empty_str`
- Idempotent cleanup (handles missing containers gracefully)
- Log streaming as a generator is memory-efficient

### Issues

#### 🔴 **Critical: `start_test_container` missing return type hint**
**Location:** Line 48-74
```python
def start_test_container(self, image: str, config_dir: str, test_id: str):
```
Should return `docker.models.containers.Container`. The docstring says it, but the signature doesn't.
**Fix:**
```python
def start_test_container(self, image: str, config_dir: str, test_id: str) -> docker.models.containers.Container:
```

#### 🟡 **Warning: No validation that config_dir exists**
**Location:** Line 65
The method accepts `config_dir` but never checks if it's readable before mounting. If the directory is deleted between validation and mount, Docker will silently fail.
**Fix:**
```python
require_non_empty_str(config_dir, "config_dir")
if not os.path.isdir(config_dir):
    raise ValueError(f"config_dir is not a readable directory: {config_dir}")
```

#### 🟡 **Warning: Image pull not explicitly called**
**Location:** Line 67-74
The `run()` call will pull the image if absent, but there's no timeout on the pull. A hung registry could block the worker indefinitely.
**Recommendation:**
Add explicit `pull()` call with timeout before `run()`, or wrap `run()` in a timeout context. Consider a configurable image pull timeout in settings.

---

## 2. **tasks.py** ⚠️ Needs Attention

### Strengths
- Clear task separation and docstrings
- Status transitions are explicit and well-ordered
- Graceful Docker socket error handling (just added)
- Proper reservation cleanup in exception handler

### Issues

#### 🔴 **Critical: run_test_bundle has race condition on artifact upload**
**Location:** Lines 226-228
```python
for fname in os.listdir(config_dir):
    if fname.endswith((...)):
        artifact_url = upload_artifact(...)  # Overwrites on each iteration
```
**Problem:** 
- Only the last artifact's URL is saved if multiple artifacts exist
- No error handling if upload fails—exception stops the loop and artifact_url stays incomplete
- Container might still be writing files while this loop runs (unlikely but possible)

**Fix:**
```python
artifact_urls = []
for fname in os.listdir(config_dir):
    if fname.endswith((".log", ".dmp", ".core", ".tar.gz")):
        try:
            url = upload_artifact(test_id, os.path.join(config_dir, fname))
            artifact_urls.append(url)
        except Exception:
            logger.exception("Failed to upload artifact %s", fname)
            # Continue uploading other artifacts; don't fail the whole run

# Persist the first artifact URL (or refactor schema to store multiple)
artifact_url = artifact_urls[0] if artifact_urls else None
_set_status(test_id, final, artifact_url=artifact_url)
```

#### 🔴 **Critical: publish_heartbeat has silent Docker failure**
**Location:** Lines 293-298
```python
live_count = 0
try:
    engine = DockerEngine()
    live_count = engine.active_test_container_count()
except Exception:
    pass
```
**Problem:**
- Any Docker error silently defaults to 0 containers, which is almost certainly wrong
- If Docker is down, the scheduler thinks the host has no capacity and won't send work
- No logging of the failure, making it invisible during debugging

**Fix:**
```python
live_count = 0
try:
    engine = DockerEngine()
    live_count = engine.active_test_container_count()
except Exception as e:
    logger.warning("Failed to query Docker for active container count: %s. Using cached count.", e)
    # Query the DB for the previous known count instead of defaulting to 0
    db_tmp = SessionLocal()
    try:
        cached_host = db_tmp.query(WorkerHost).filter_by(host_id=settings.worker_host_id).one_or_none()
        live_count = cached_host.active_containers if cached_host else 0
    finally:
        db_tmp.close()
```

#### 🟡 **Warning: run_test_bundle doesn't wait for container cleanup before updating status**
**Location:** Lines 242-247
The finally block runs container cleanup AFTER status update. If the cleanup fails, the test is marked done but the container is still running.
**Better order:**
```python
finally:
    engine.cleanup(test_id)  # Remove container first
    _decrement_active_containers(settings.worker_host_id)
    influx.close()
    # Then the status is already set above
```
Currently this is correct (status set at line 231, cleanup in finally), but the flow is easy to mess up. Add a comment to clarify why.

#### 🟡 **Warning: LogLine batch commit interval is arbitrary**
**Location:** Line 216-217
```python
if line_no % 25 == 0:
    db.commit()
```
No justification for the 25-line interval. Too sparse could hit DB connection timeout on long tests; too dense causes excessive DB load.
**Recommendation:**
Make this configurable or tie it to wall-clock time (e.g., commit every 5 seconds) and add a log at the end of the loop to ensure final commit happens.

#### 🟡 **Warning: Quali reservation release has no retry**
**Location:** Lines 237-240
If the Quali server is temporarily down, the reservation is leaked. The exception is logged but not retried.
**Recommendation:**
Add exponential backoff retry or consider a separate cleanup job that periodically releases stale reservations.

#### 🟡 **Warning: reap_stale cutoff is hardcoded**
**Location:** Line 331
```python
cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
```
2 hours is reasonable but inflexible. If test framework is slow, this might incorrectly mark runs as stuck.
**Recommendation:**
Move to settings with a sensible default, document in CLAUDE.md / README.

#### 🟡 **Warning: No idempotency check on status updates**
**Location:** Lines 129-139 (_set_status)
If `_set_status` is called twice with different statuses, the second one wins. No audit trail or warning.
**Recommendation:**
Add a check: if status is moving backward in the state machine (e.g., FAILED → RUNNING), log a warning and skip the update.

---

## 3. **artifact_uploader.py** ✅ Good

### Strengths
- Clean, single-responsibility function
- Proper input validation
- Good error surface (lets callers decide retry strategy)
- File existence check before upload

### Issues

#### 🟡 **Warning: No authentication failure logging**
**Location:** Line 50
```python
resp.raise_for_status()
```
If the Artifactory token is invalid, the error is raised but not logged with context. Makes debugging harder.
**Fix:**
```python
try:
    resp.raise_for_status()
except requests.HTTPError as e:
    logger.error("Artifactory upload failed for %s (%s): %s", test_id, file_path, e.response.status_code)
    raise
```

#### 🟡 **Warning: Timeout is generous (120s)**
**Location:** Line 48
May be appropriate for large artifacts, but no fallback if the upload is truly stuck. Consider progress callback to detect stalled transfers.

#### 🟡 **Minor: No cleanup of local artifact after successful upload**
Some systems prefer to keep test artifacts locally for a while before expiring them. Current code keeps them in `config_dir` indefinitely. Document this in code or consider adding a TTL cleanup task.

---

## 4. **translation_layer.py** ✅ Good

### Strengths
- Clean plugin architecture (easy to add new runners)
- Proper input validation
- Config directory auto-creation
- Clear separation between base and implementations

### Issues

#### 🟡 **Warning: PytestRunner timeout_seconds cast is unsafe**
**Location:** Line 87
```python
"timeout_seconds": int(self.variables.get("timeout_seconds", 600)),
```
If the UI sends a non-numeric string, `int()` will crash. Should validate first.
**Fix:**
```python
timeout_str = self.variables.get("timeout_seconds", "600")
try:
    timeout_seconds = int(timeout_str)
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
except (ValueError, TypeError):
    raise ValueError(f"Invalid timeout_seconds: {timeout_str}")
```

#### 🟡 **Warning: RobotRunner doesn't validate tags**
**Location:** Lines 109-111
If `include_tags` contains shell metacharacters or newlines, the generated `robot.args` file could be corrupted.
**Recommendation:**
Escape or validate tags. For now, document that tags must be alphanumeric.

#### 🟡 **Warning: No file write error handling**
**Location:** Lines 91-92 (PytestRunner), 113-114 (RobotRunner)
If the disk is full or the directory becomes read-only between creation and write, the exception propagates uncaught.
**Fix:**
```python
try:
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
except IOError as e:
    raise RuntimeError(f"Failed to write config to {out}: {e}")
```

---

## 5. **celery_app.py** ✅ Good

### Strengths
- Clean, minimal config
- Proper task naming
- Beat schedule and worker queue per-host (good for scaling)
- Schedule stored in writable location (`/tmp`)

### Issues

#### 🟡 **Warning: broker_connection_retry_on_startup deprecation warning**
The logs show:
```
CPendingDeprecationWarning: The broker_connection_retry configuration setting will no longer determine whether broker connection retries are made during startup in Celery 6.0
```
**Fix:** Add to `celery_app.conf`:
```python
celery_app.conf.broker_connection_retry_on_startup = True
```

---

## 6. **Cross-Cutting Concerns**

### 🔴 **Critical: No connection pooling for RabbitMQ**
**Location:** `_publish_fanout()` in tasks.py, lines 59-70
Opens a new connection for each log line, status update, and heartbeat reply. Under high load (many tests with many log lines), this will exhaust RabbitMQ connections.
**Fix:**
Use a connection pool or a persistent connection per worker (celery's built-in broker connection):
```python
def _publish_fanout(exchange_name: str, body: str) -> None:
    # Use celery's async publish for fanouts instead of pika directly
    from celery import current_app
    current_app.send_task('worker.publish_to_fanout', 
                          args=(exchange_name, body), 
                          routing_key=None)
```
Or cache a single pika connection per worker process.

### 🔴 **Critical: No DB connection pool exhaustion handling**
Each task opens a new `SessionLocal()` connection. Under load, this can exhaust the pool. No retry or backoff.
**Recommendation:**
- Increase DB pool size in settings
- Add task retry decorator to Celery tasks with exponential backoff
- Monitor pool utilization in metrics

### 🟡 **Warning: Timestamps are UTC but never validated for clock skew**
If a worker's clock drifts, heartbeat timestamps could be in the past or far future, confusing the scheduler.
**Recommendation:**
Log a warning if `last_heartbeat` is >5 minutes in the future or past.

### 🟡 **Warning: No observability hooks**
No structured logging, no metrics export (e.g., task duration, error rates). Makes debugging production issues hard.
**Recommendation:**
Add prometheus metrics or datadog integration; log task start/end with durations.

---

## 7. **Recommended Changes (Priority Order)**

### High Priority (Do before merging)
1. ✅ Fix `run_test_bundle` artifact upload loop (handle multiple artifacts, catch upload errors)
2. ✅ Fix `publish_heartbeat` Docker exception handling (log warning, use cached count)
3. ✅ Add connection pooling for RabbitMQ publishes
4. ✅ Add `timeout_seconds` validation in PytestRunner
5. ✅ Add `broker_connection_retry_on_startup` to celery config

### Medium Priority (Before production)
6. Add file write error handling in translation_layer
7. Add artifact upload error logging in artifact_uploader
8. Make reap_stale cutoff configurable
9. Add status transition validation (no backward transitions)
10. Instrument tasks with prometheus metrics

### Low Priority (Nice-to-have)
11. Document timeout expectations and CLI for runners
12. Add progress callbacks for large artifact uploads
13. Consider TTL cleanup for local test artifacts
14. Add observability for RabbitMQ connection health

---

## Testing Checklist

Before shipping, verify:
- [ ] Docker socket unavailable → heartbeat logs warning, doesn't crash
- [ ] Multiple artifacts uploaded → all are persisted (update schema if needed)
- [ ] Artifact upload fails → run is marked FAILED, other artifacts still uploaded
- [ ] Test output >1000 lines → all logged, DB commits batched correctly
- [ ] Quali reservation release fails → error logged, doesn't crash the task
- [ ] Invalid runner_type → clear error message, not generic KeyError
- [ ] config_dir deleted mid-run → Docker error is caught and logged
- [ ] RabbitMQ temporarily down → connection retries, doesn't lose messages
- [ ] Clock skew (system time jumps backward) → tasks still complete correctly
- [ ] 100 concurrent logs → RabbitMQ doesn't hit connection limits

---
