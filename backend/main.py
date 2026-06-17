"""
FastAPI application: HTTP routes, LDAP login, the live-log and admin WebSockets,
and the dispatch entry point.

Live updates use HTMX + the WebSocket extension: the server pushes HTML fragments
that HTMX swaps into the page via out-of-band (hx-swap-oob) targets.

The admin "Get Host Status" feature fans a status request out to every host's
queue, then streams replies back to the browser as they arrive, emitting a soft
"polling complete (N of M)" sentinel after a grace period.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

import aio_pika
from fastapi import Depends, FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from backend.auth import LDAPAuthError, authenticate_ldap, resolve_session
from backend.celery_client import celery_client
from backend.config import get_settings
from backend.database import SessionLocal, get_db, init_db
from backend.models import TestExecution, User, WorkerHost
from backend.services.maintenance import run_maintenance_sweep
from backend.services.scheduler import dispatch, queue_name_for
from backend.services.status_render import render_sentinel, render_status_row
from shared.schemas import TestBundleRequest, TestStatus

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan: init the schema and run the central resiliency sweep.

    The sweep loop is the worker-independent half of spec §D — it marks hosts
    whose heartbeat has gone stale as offline and fails any runs orphaned on
    them, from the always-on backend rather than the (possibly dead) worker.

    Args:
        app: The FastAPI application (unused; required by the lifespan protocol).

    Yields:
        None. Control returns to the framework for the lifetime of the app; the
        background sweep task is cancelled cleanly on shutdown.
    """
    init_db()
    stop = asyncio.Event()
    sweep_task = asyncio.create_task(_maintenance_loop(stop))
    try:
        yield
    finally:
        stop.set()
        sweep_task.cancel()
        try:
            await sweep_task
        except asyncio.CancelledError:
            pass


async def _maintenance_loop(stop: asyncio.Event) -> None:
    """
    Periodically run the (blocking) maintenance sweep off the event loop.

    Args:
        stop: Event signalled on shutdown to end the loop promptly.

    Returns:
        None. Runs until ``stop`` is set; sweep errors are logged, not fatal.
    """
    interval = settings.maintenance_sweep_seconds
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_maintenance_sweep)
        except Exception:  # noqa: BLE001 - a sweep failure must not kill the loop
            logger.exception("maintenance sweep failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


app = FastAPI(title="test_orch", lifespan=lifespan)
templates = Jinja2Templates(directory="backend/templates")


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _amqp_url() -> str:
    """
    Build the AMQP connection URL from settings.

    Args:
        None.

    Returns:
        str: An ``amqp://user:pass@host:port/`` URL for aio-pika.
    """
    return (
        f"amqp://{settings.rabbitmq_user}:{settings.rabbitmq_password}"
        f"@{settings.rabbitmq_host}:{settings.rabbitmq_port}/"
    )


def log_exchange_for(test_id: str) -> str:
    """
    Return the fanout exchange name carrying a test's live log lines.

    Args:
        test_id: The test identifier.

    Returns:
        str: ``"test_logs.<test_id>"``.
    """
    return f"test_logs.{test_id}"


def status_exchange_for(correlation_id: str) -> str:
    """
    Return the fanout exchange name carrying one poll's host-status replies.

    Args:
        correlation_id: The unique per-poll correlation id.

    Returns:
        str: ``"host_status.<correlation_id>"``.
    """
    return f"host_status.{correlation_id}"


def _escape(text: str) -> str:
    """
    HTML-escape ``&``, ``<``, ``>`` for safe embedding in pushed fragments.

    Args:
        text: Raw text to escape.

    Returns:
        str: The escaped text.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------- #
# Auth dependency
# --------------------------------------------------------------------------- #
def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """
    Resolve the logged-in user from the session cookie (request dependency).

    Args:
        request: The incoming HTTP request (for cookies).
        db: Active SQLAlchemy session.

    Returns:
        User | None: The authenticated user, or ``None`` if no valid session.
    """
    return resolve_session(db, request.cookies.get("session_token"))


# --------------------------------------------------------------------------- #
# HTTP routes
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: User | None = Depends(current_user)) -> HTMLResponse:
    """
    Render the engineer dashboard (or redirect to login if unauthenticated).

    Args:
        request: The incoming HTTP request.
        user: The resolved current user, if any.

    Returns:
        HTMLResponse: The dashboard page, or a redirect to ``/login``.
    """
    if user is None:
        return RedirectResponse("/login", status_code=302)
    db = SessionLocal()
    try:
        runs = (
            db.query(TestExecution)
            .filter_by(user_id=user.id)
            .order_by(TestExecution.created_at.desc())
            .limit(50)
            .all()
        )
    finally:
        db.close()
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "user": user, "runs": runs}
    )


@app.post("/tests")
def submit_test(
    runner_type: str = Form(...),
    framework_image: str = Form(...),
    target_dut: str = Form(""),
    required_hw_tags: str = Form(""),
    requires_emulation: bool = Form(False),
    user: User | None = Depends(current_user),
):
    """
    Create and dispatch a test bundle for the logged-in engineer.

    Builds a validated :class:`TestBundleRequest`, persists a ``PENDING``
    ``TestExecution`` (the audit record), then runs the filter-and-rank
    :func:`dispatch`. The row is advanced to ``DISPATCHED`` with the chosen host,
    or marked ``FAILED`` if no host currently qualifies.

    Args:
        runner_type: Registered runner key (e.g. ``"pytest"`` or ``"robot"``).
        framework_image: Fully-qualified container image for the run.
        target_dut: Optional device-under-test identifier passed to the runner.
        required_hw_tags: Comma-separated hardware tags the host must carry.
        requires_emulation: Whether the host must support emulation.
        user: The resolved current user (audit identity).

    Returns:
        Response: Redirect to the dashboard, or to ``/login`` if unauthenticated.
    """
    if user is None:
        return RedirectResponse("/login", status_code=302)

    test_id = uuid.uuid4().hex
    tags = [t.strip() for t in required_hw_tags.split(",") if t.strip()]
    runner_variables = {"target_dut": target_dut} if target_dut else {}

    try:
        bundle = TestBundleRequest(
            test_id=test_id,
            user_id=user.id,
            runner_type=runner_type,
            framework_image=framework_image,
            required_hw_tags=tags,
            requires_emulation=requires_emulation,
            runner_variables=runner_variables,
        )
    except (TypeError, ValueError):
        # Malformed input — bounce back to the dashboard rather than 500.
        return RedirectResponse("/", status_code=302)

    db = SessionLocal()
    try:
        execution = TestExecution(
            test_id=test_id,
            user_id=user.id,
            runner_type=runner_type,
            framework_image=framework_image,
            status=TestStatus.PENDING.value,
        )
        db.add(execution)
        db.commit()

        try:
            chosen = dispatch(db, bundle)
        except Exception:  # noqa: BLE001 - broker/DB error must not 500 the form
            logger.exception("dispatch failed for test %s", test_id)
            chosen = None

        if chosen is None:
            execution.status = TestStatus.FAILED.value
        else:
            execution.status = TestStatus.DISPATCHED.value
            execution.target_host_id = chosen
        db.commit()
    finally:
        db.close()

    return RedirectResponse("/", status_code=302)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> HTMLResponse:
    """
    Render the login form.

    Args:
        request: The incoming HTTP request.

    Returns:
        HTMLResponse: The login page with no error banner.
    """
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login_submit(
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """
    Authenticate a login submission and set the session cookie.

    Args:
        username: Submitted username.
        password: Submitted password.
        db: Active SQLAlchemy session.

    Returns:
        Response: A redirect to ``/`` with the ``session_token`` cookie set on
        success, or the re-rendered login page with an error on failure.
    """
    try:
        _user, session = authenticate_ldap(db, username, password)
    except (LDAPAuthError, ValueError, TypeError):
        return templates.TemplateResponse(
            "login.html",
            {"request": {}, "error": "Invalid credentials or directory unavailable."},
            status_code=401,
        )
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(
        "session_token", session.token, httponly=True, samesite="lax", max_age=12 * 3600
    )
    return resp


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, user: User | None = Depends(current_user)) -> HTMLResponse:
    """
    Render the admin panel showing host inventory and the live-status poller.

    Args:
        request: The incoming HTTP request.
        user: The resolved current user, if any.

    Returns:
        HTMLResponse: The admin page, or a redirect to ``/login``.
    """
    if user is None:
        return RedirectResponse("/login", status_code=302)
    db = SessionLocal()
    try:
        hosts = db.query(WorkerHost).order_by(WorkerHost.host_id).all()
    finally:
        db.close()
    return templates.TemplateResponse(
        "admin.html", {"request": request, "user": user, "hosts": hosts}
    )


# --------------------------------------------------------------------------- #
# Live log streaming
# --------------------------------------------------------------------------- #
async def _consume_fanout(exchange_name, websocket, render, on_message=None):
    """
    Bind a temp queue to a fanout exchange and stream messages to a WebSocket.

    Args:
        exchange_name: Name of the fanout exchange to bind.
        websocket: The connected client WebSocket to push fragments to.
        render: Callable ``(body: str) -> str`` producing the HTML fragment to
            send (empty string suppresses the send).
        on_message: Optional async callable invoked once per message AFTER it is
            sent (used to bump the sentinel reply counter).

    Returns:
        None. Runs until cancelled or the queue iterator ends.

    Note:
        The AMQP connection is closed in a ``finally`` so cancelling the task
        mid-iteration cannot leak the connection.
    """
    connection = await aio_pika.connect_robust(_amqp_url())
    try:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            exchange_name, aio_pika.ExchangeType.FANOUT, durable=False
        )
        queue = await channel.declare_queue("", exclusive=True)
        await queue.bind(exchange)
        async with queue.iterator() as it:
            async for message in it:
                async with message.process():
                    body = message.body.decode("utf-8", errors="replace")
                    fragment = render(body)
                    if fragment:
                        await websocket.send_text(fragment)
                    if on_message is not None:
                        await on_message()
    finally:
        await connection.close()  # ensure cleanup even on CancelledError


@app.websocket("/ws/logs/{test_id}")
async def ws_logs(websocket: WebSocket, test_id: str):
    """
    Stream a test's live container logs to the browser over a WebSocket.

    Args:
        websocket: The client WebSocket connection.
        test_id: The test whose log fanout exchange to subscribe to.

    Returns:
        None. Runs until the client disconnects; the consumer task is always
        cancelled on exit.

    Note:
        If the consumer task dies (e.g. broker outage) it is detected via
        ``done()`` and the socket is closed rather than hanging open silently.
    """
    await websocket.accept()

    def render(line: str) -> str:
        return (
            '<div id="terminal" hx-swap-oob="beforeend">'
            f'<div class="log-line">{_escape(line)}</div></div>'
        )

    consumer = asyncio.create_task(
        _consume_fanout(log_exchange_for(test_id), websocket, render)
    )
    try:
        while True:
            if consumer.done():  # surface consumer failure instead of hanging
                break
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
            except asyncio.TimeoutError:
                continue  # periodic wake to re-check consumer health
    except WebSocketDisconnect:
        pass
    finally:
        consumer.cancel()


# --------------------------------------------------------------------------- #
# Admin live host-status poll
# --------------------------------------------------------------------------- #
class _PollState:
    """Coordinates sentinel emission between the consumer and the grace timer."""

    def __init__(self, websocket, total):
        """
        Initialize per-poll coordination state.

        Args:
            websocket: The admin WebSocket to send sentinel fragments on.
            total: Number of hosts this poll was dispatched to.

        Returns:
            None.
        """
        self._ws = websocket
        self.total = total
        self.received = 0
        self._completed = False
        self._lock = asyncio.Lock()

    async def on_reply(self) -> None:
        """
        Record a received reply and upgrade the sentinel if now complete.

        Args:
            None.

        Returns:
            None. Emits a final "all responded" sentinel when ``received``
            reaches ``total``.
        """
        self.received += 1
        if self.total and self.received >= self.total:
            await self.emit_sentinel(final=True)

    async def emit_sentinel(self, *, final: bool) -> None:
        """
        Send the sentinel fragment exactly once, latching on the first emission.

        Args:
            final: Advisory flag describing the caller's intent (``True`` from the
                completing reply, ``False`` from the grace timer). It does NOT
                gate latching — see the note.

        Returns:
            None. No-op once a sentinel has already been emitted.

        Note:
            The latch is set on the FIRST emission regardless of ``final``, so the
            grace-timer's partial sentinel and a completing straggler can never
            both render. Whichever fires first wins under any interleaving; the
            other observes ``_completed`` inside the lock and returns.
        """
        async with self._lock:
            if self._completed:
                return
            await self._ws.send_text(
                render_sentinel(received=self.received, total=self.total)
            )
            self._completed = True


async def _emit_sentinel_after_grace(state, grace, is_current):
    """
    Emit the soft poll sentinel after a grace period, if still the current poll.

    Args:
        state: The :class:`_PollState` tracking reply counts for this poll.
        grace: Seconds to wait before emitting the sentinel.
        is_current: Zero-arg callable returning ``True`` while this poll's
            generation is still active; a superseded poll suppresses its sentinel.

    Returns:
        None.
    """
    try:
        await asyncio.sleep(grace)
        if is_current():
            await state.emit_sentinel(final=False)
    except asyncio.CancelledError:
        pass


@app.websocket("/ws/admin")
async def ws_admin(websocket: WebSocket):
    """
    Admin live host-status channel with generation-guarded re-polling.

    On each inbound "poll" frame this mints a new correlation id AND a new
    generation token. Only the consumer/timer belonging to the current
    generation may render, so a late reply from a previous (cancelled) poll can
    never cross-render into a freshly-cleared table.

    Args:
        websocket: The admin client WebSocket connection.

    Returns:
        None. Runs until the client disconnects; in-flight tasks are cancelled.
    """
    await websocket.accept()

    token = websocket.cookies.get("session_token")
    db = SessionLocal()
    try:
        user = resolve_session(db, token)
    finally:
        db.close()
    if user is None:
        await websocket.close(code=4401)
        return

    consumer_task: asyncio.Task | None = None
    sentinel_task: asyncio.Task | None = None
    # Monotonic generation counter; bumped on every poll. Captured by closures so
    # stale tasks can detect they are no longer current and self-suppress.
    generation = {"current": 0}

    def _cancel_inflight() -> None:
        for t in (consumer_task, sentinel_task):
            if t and not t.done():
                t.cancel()

    try:
        while True:
            await websocket.receive_text()  # any frame == "poll now"
            _cancel_inflight()

            generation["current"] += 1
            my_gen = generation["current"]
            correlation_id = uuid.uuid4().hex

            db = SessionLocal()
            try:
                host_ids = [h.host_id for h in db.query(WorkerHost).all()]
            finally:
                db.close()
            total = len(host_ids)

            await websocket.send_text(
                '<tbody id="host-status-results" hx-swap-oob="innerHTML">'
                f'<tr><td colspan="5">Waiting for {total} runner(s) to respond...</td></tr>'
                "</tbody>"
            )
            await websocket.send_text(
                '<div id="poll-sentinel" hx-swap-oob="innerHTML"></div>'
            )

            state = _PollState(websocket, total)

            def _is_current() -> bool:
                """True while this poll is still the active generation."""
                return generation["current"] == my_gen

            async def _guarded_on_reply() -> None:
                """Count + render a reply only if this poll is still current."""
                if _is_current():
                    await state.on_reply()

            def _guarded_render(body: str) -> str:
                """Suppress row rendering for superseded polls."""
                return render_status_row(body) if _is_current() else ""

            consumer_task = asyncio.create_task(
                _consume_fanout(
                    status_exchange_for(correlation_id),
                    websocket,
                    _guarded_render,
                    on_message=_guarded_on_reply,
                )
            )

            for host_id in host_ids:
                celery_client.send_task(
                    "worker.report_status",
                    args=[correlation_id],
                    queue=queue_name_for(host_id),
                )

            sentinel_task = asyncio.create_task(
                _emit_sentinel_after_grace(
                    state, settings.host_poll_grace_seconds, _is_current
                )
            )
    except WebSocketDisconnect:
        pass
    finally:
        _cancel_inflight()