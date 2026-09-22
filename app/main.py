from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import create_schema, get_db
from app.demo_security import (
    COOKIE_AGE_SECONDS,
    COOKIE_NAME,
    DEMO_ADDRESS,
    checked_demo_date,
    checked_demo_time,
    demo_idempotency_key,
    memory_owner,
    new_session_token,
    session_id_from_token,
    session_user_id,
    task_owner,
    validate_demo_settings,
)
from app.domain import TaskStatus
from app.models import Memory, Task, TaskEvent
from app.schemas import (
    ApprovalRequest,
    MemoryRead,
    MemoryUpsert,
    MetricsRead,
    OperatorAction,
    ResolveRequest,
    TaskCreate,
    TaskRead,
    VoiceInterpretRead,
    VoiceInterpretRequest,
)
from app.services import (
    add_event,
    approval_reason,
    approve_task,
    assign_operator,
    cancel_task,
    confirm_booking,
    create_task,
    get_task_or_404,
    metrics,
    resolve_task,
    run_task,
    transition,
    upsert_memory,
)
from app.voice_intake import interpret_request


@asynccontextmanager
async def lifespan(_: FastAPI) -> Iterator[None]:
    validate_demo_settings(get_settings())
    create_schema()
    yield


app = FastAPI(
    title="Jeevan API",
    version="0.1.0",
    description="Voice and browser agent for completing household bookings",
    lifespan=lifespan,
)

WEB_DIR = Path(__file__).parent / "web"
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
templates = Jinja2Templates(directory=WEB_DIR / "templates")


@app.middleware("http")
async def public_demo_guard(request: Request, call_next):
    settings = get_settings()
    if not settings.public_demo_mode:
        return await call_next(request)
    validate_demo_settings(settings)
    path = request.url.path
    if (
        path in {"/docs", "/redoc", "/openapi.json", "/demo-provider"}
        or path.startswith("/api/v1/ops/")
        or path.startswith("/api/v1/demo/")
        or path.startswith("/api/v1/webhooks/")
    ):
        return JSONResponse({"detail": "Not available in the public demo"}, status_code=404)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        allowed = urlsplit(settings.public_base_url)
        if origin:
            actual = urlsplit(origin)
            if (actual.scheme, actual.netloc) != (allowed.scheme, allowed.netloc):
                return JSONResponse({"detail": "Cross-origin request denied"}, status_code=403)
    session_id = session_id_from_token(request.cookies.get(COOKIE_NAME), settings)
    new_token = None
    if session_id is None:
        session_id, new_token = new_session_token(settings)
    request.state.demo_user_id = f"demo:{session_id}"
    response = await call_next(request)
    if new_token:
        response.set_cookie(
            COOKIE_NAME,
            new_token,
            max_age=COOKIE_AGE_SECONDS,
            httponly=True,
            secure=settings.public_base_url.startswith("https://"),
            samesite="lax",
            path="/",
        )
    if path.startswith("/api/") or path == "/":
        response.headers["Cache-Control"] = "no-store"
        response.headers["Vary"] = "Cookie"
    return response


def as_task(task: Task) -> TaskRead:
    return TaskRead.model_validate(task)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "booking_adapter": get_settings().booking_adapter,
            "public_demo_mode": get_settings().public_demo_mode,
        },
    )


@app.get("/demo-provider", response_class=HTMLResponse, include_in_schema=False)
def provider_portal(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="provider.html", context={})


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "jeevan-api"}


@app.get("/ready", tags=["system"])
def ready(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(select(1))
    return {"status": "ready"}


@app.post("/api/v1/voice/interpret", response_model=VoiceInterpretRead, tags=["voice"])
def interpret_voice_request(
    payload: VoiceInterpretRequest, request: Request, db: Session = Depends(get_db)
) -> VoiceInterpretRead:
    """Prepare a reviewable draft from speech; never executes a booking."""
    if get_settings().public_demo_mode:
        result = interpret_request(payload.transcript, saved_address=DEMO_ADDRESS)
        result.draft.address = DEMO_ADDRESS
        result.missing_fields = [field for field in result.missing_fields if field != "address"]
        result.ready = not result.missing_fields
        if result.ready:
            result.reply = (
                f"I heard {result.draft.service_type} on {result.draft.requested_date}, "
                f"{result.draft.time_window.lower()}, with a maximum budget of "
                f"₹{result.draft.budget_rupees:,} at a fictional demo address. "
                "Please review these details before I start."
            )
        return result
    memory = db.scalar(
        select(Memory).where(Memory.user_id == payload.user_id, Memory.key == "home_address")
    )
    return interpret_request(payload.transcript, saved_address=memory.value if memory else None)


@app.post(
    "/api/v1/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    tags=["tasks"],
)
def create_new_task(
    payload: TaskCreate, response: Response, request: Request, db: Session = Depends(get_db)
) -> TaskRead:
    if get_settings().public_demo_mode:
        owner = session_user_id(request)
        requested_date = checked_demo_date(payload.requested_date)
        time_window = checked_demo_time(payload.time_window)
        if payload.budget_rupees is None or payload.budget_rupees > 100_000:
            raise HTTPException(status_code=422, detail="A demo budget of ₹1–100,000 is required")
        payload = payload.model_copy(update={
            "user_id": owner,
            "idempotency_key": demo_idempotency_key(owner, payload.idempotency_key),
            "description": (
                f"Book AC servicing on {requested_date}, {time_window}, "
                f"under ₹{payload.budget_rupees:,} (simulated demo)."
            ),
            "service_type": "AC servicing",
            "requested_date": requested_date,
            "time_window": time_window,
            "address": DEMO_ADDRESS,
            "phone": None,
        })
        existing = db.scalar(select(Task.id).where(Task.idempotency_key == payload.idempotency_key))
        if not existing:
            settings = get_settings()
            session_count = db.scalar(
                select(func.count(Task.id)).where(Task.user_id == owner)
            ) or 0
            total_count = db.scalar(
                select(func.count(Task.id)).where(Task.user_id.like("demo:%"))
            ) or 0
            if session_count >= settings.demo_max_tasks_per_session:
                raise HTTPException(
                    status_code=429, detail="Demo task limit reached for this session"
                )
            if total_count >= settings.demo_max_tasks_total:
                raise HTTPException(status_code=503, detail="Demo capacity reached")
    task, created = create_task(db, payload)
    if not created:
        response.status_code = status.HTTP_200_OK
        response.headers["X-Idempotent-Replay"] = "true"
    return as_task(task)


@app.get("/api/v1/tasks", response_model=list[TaskRead], tags=["tasks"])
def list_tasks(
    request: Request,
    task_status: str | None = Query(default=None, alias="status"),
    user_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[TaskRead]:
    query = (
        select(Task)
        .options(selectinload(Task.events))
        .order_by(Task.updated_at.desc())
        .limit(limit)
    )
    if task_status:
        query = query.where(Task.status == task_status)
    owner = task_owner(request, user_id)
    if owner:
        query = query.where(Task.user_id == owner)
    return [as_task(item) for item in db.scalars(query).all()]


@app.get("/api/v1/tasks/{task_id}", response_model=TaskRead, tags=["tasks"])
def get_task(
    task_id: str, request: Request, user_id: str | None = None, db: Session = Depends(get_db)
) -> TaskRead:
    return as_task(get_task_or_404(db, task_id, user_id=task_owner(request, user_id)))


@app.post("/api/v1/tasks/{task_id}/run", response_model=TaskRead, tags=["tasks"])
def start_task(task_id: str, request: Request, db: Session = Depends(get_db)) -> TaskRead:
    return as_task(run_task(db, get_task_or_404(db, task_id, user_id=task_owner(request))))


@app.post("/api/v1/tasks/{task_id}/retry", response_model=TaskRead, tags=["tasks"])
def retry_task(task_id: str, request: Request, db: Session = Depends(get_db)) -> TaskRead:
    task = get_task_or_404(db, task_id, user_id=task_owner(request))
    if task.status != TaskStatus.RETRY_SCHEDULED.value:
        raise HTTPException(status_code=409, detail="Task is not scheduled for retry")
    return as_task(run_task(db, task))


@app.post("/api/v1/tasks/{task_id}/approve", response_model=TaskRead, tags=["tasks"])
def approve_current_quote(
    task_id: str, payload: ApprovalRequest, request: Request, db: Session = Depends(get_db)
) -> TaskRead:
    task = get_task_or_404(db, task_id, user_id=task_owner(request))
    return as_task(
        approve_task(db, task, approved=payload.approved, task_version=payload.task_version)
    )


@app.post("/api/v1/tasks/{task_id}/cancel", response_model=TaskRead, tags=["tasks"])
def cancel_current_task(task_id: str, request: Request, db: Session = Depends(get_db)) -> TaskRead:
    return as_task(cancel_task(db, get_task_or_404(db, task_id, user_id=task_owner(request))))


@app.post("/api/v1/ops/tasks/{task_id}/takeover", response_model=TaskRead, tags=["operations"])
def take_over_task(
    task_id: str, payload: OperatorAction, db: Session = Depends(get_db)
) -> TaskRead:
    return as_task(
        assign_operator(db, get_task_or_404(db, task_id), payload.operator, payload.note)
    )


@app.post("/api/v1/ops/tasks/{task_id}/resolve", response_model=TaskRead, tags=["operations"])
def resolve_current_task(
    task_id: str, payload: ResolveRequest, db: Session = Depends(get_db)
) -> TaskRead:
    return as_task(
        resolve_task(
            db,
            get_task_or_404(db, task_id),
            operator=payload.operator,
            note=payload.note,
            quote_rupees=payload.quote_rupees,
            quoted_slot=payload.quoted_slot,
        )
    )


@app.get("/api/v1/users/{user_id}/memories", response_model=list[MemoryRead], tags=["memory"])
def list_memories(
    user_id: str, request: Request, db: Session = Depends(get_db)
) -> list[MemoryRead]:
    owner = memory_owner(request, user_id)
    if get_settings().public_demo_mode:
        now = datetime.now(UTC)
        return [MemoryRead(
            id=f"synthetic-{owner.removeprefix('demo:')}",
            user_id=owner,
            key="home_address",
            value=DEMO_ADDRESS,
            kind="explicit",
            source="public demo",
            durable=False,
            created_at=now,
            updated_at=now,
        )]
    return list(db.scalars(select(Memory).where(Memory.user_id == owner).order_by(Memory.key)))


@app.put("/api/v1/users/{user_id}/memories/{key}", response_model=MemoryRead, tags=["memory"])
def save_memory(
    user_id: str, key: str, payload: MemoryUpsert, request: Request, db: Session = Depends(get_db)
) -> Memory:
    if get_settings().public_demo_mode:
        raise HTTPException(status_code=403, detail="Memory editing is disabled in the public demo")
    return upsert_memory(db, user_id, key, payload)


@app.delete(
    "/api/v1/users/{user_id}/memories/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["memory"],
)
def delete_memory(
    user_id: str, key: str, request: Request, db: Session = Depends(get_db)
) -> Response:
    if get_settings().public_demo_mode:
        raise HTTPException(status_code=403, detail="Memory editing is disabled in the public demo")
    memory = db.scalar(select(Memory).where(Memory.user_id == user_id, Memory.key == key))
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    db.delete(memory)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/v1/ops/metrics", response_model=MetricsRead, tags=["operations"])
def get_metrics(db: Session = Depends(get_db)) -> dict[str, int | float]:
    return metrics(db)


@app.post("/api/v1/demo/seed", tags=["demo"])
def seed_demo(db: Session = Depends(get_db)) -> dict[str, str]:
    upsert_memory(
        db,
        "demo-user",
        "home_address",
        MemoryUpsert(value="18th Main, HSR Layout, Bengaluru", source="demo user"),
    )
    upsert_memory(
        db,
        "demo-user",
        "usual_availability",
        MemoryUpsert(value="Weekends after 2:00 PM", source="demo user"),
    )
    return {"status": "seeded", "user_id": "demo-user"}


@app.post("/api/v1/webhooks/livekit", tags=["voice"])
def livekit_event(
    payload: dict,
    x_jeevan_signature: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> TaskRead:
    """Receive idempotent structured events from the separately deployed LiveKit worker."""
    settings = get_settings()
    if x_jeevan_signature != settings.jeevan_webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    task_id = str(payload.get("task_id", ""))
    event_id = str(payload.get("event_id", ""))
    event_type = str(payload.get("event_type", ""))
    if not task_id or not event_id or not event_type:
        raise HTTPException(
            status_code=422, detail="task_id, event_id, and event_type are required"
        )

    dedupe_type = f"livekit.{event_id}"[:80]
    existing = db.scalar(
        select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.event_type == dedupe_type)
    )
    task = get_task_or_404(db, task_id)
    if existing:
        return as_task(task)

    add_event(
        db,
        task,
        dedupe_type,
        str(payload.get("text") or event_type),
        actor=str(payload.get("speaker") or "livekit"),
        data={"source_event_type": event_type},
    )
    if event_type == "quote":
        if task.status != TaskStatus.CALLING.value:
            raise HTTPException(status_code=409, detail="Quote received outside an active call")
        try:
            quote_rupees = int(payload["quote_rupees"])
            slot = str(payload["slot"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail="Valid quote_rupees and slot required"
            ) from exc
        if quote_rupees <= 0 or not slot:
            raise HTTPException(status_code=422, detail="Valid quote_rupees and slot required")
        task.provider_name = str(payload.get("provider_name") or "Provider")
        task.quote_paise = quote_rupees * 100
        task.quoted_slot = slot
        transition(db, task, TaskStatus.QUOTE_RECEIVED, "Live call returned a structured quote")
        reason = approval_reason(task)
        if reason:
            task.attention_reason = reason
            transition(db, task, TaskStatus.AWAITING_APPROVAL, "Offer requires user approval")
        else:
            transition(db, task, TaskStatus.BOOKING, "Quote is within the approved constraints")
            return as_task(confirm_booking(db, task))
    elif event_type == "call_failed":
        retryable = bool(payload.get("retryable", False))
        target = (
            TaskStatus.RETRY_SCHEDULED
            if retryable and task.attempt_count < settings.max_call_attempts
            else TaskStatus.NEEDS_HUMAN
        )
        task.attention_reason = str(payload.get("reason") or "Live call failed")
        transition(db, task, target, task.attention_reason)

    db.commit()
    return as_task(get_task_or_404(db, task.id))
