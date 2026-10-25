"""SSE event stream with replay by sequence (CP-EVT-005 … 008)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from starlette.requests import Request as StarletteRequest

from factory_control import iam
from factory_control import store as S
from factory_control.events.bus import EventBus
from factory_control.events.sse import parse_last_event_id, stream_workflow_events
from factory_control.rest.deps import authorize, operator_of, runtime_of

router = APIRouter(prefix="/api/v1", tags=["events"])


@router.get(
    "/workflows/{workflow_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "An ``text/event-stream`` of S1 events, replayable by sequence.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def events(
    request: StarletteRequest, workflow_id: str, after: int | None = Query(default=None)
) -> StreamingResponse:
    runtime = runtime_of(request)
    bus: EventBus = request.app.state.bus
    with runtime.transaction(operator=operator_of(request)) as ctx:
        workflow = S.get_workflow(ctx.session, workflow_id)
        authorize(request, iam.PROJECT_VIEW, workflow.project_id)

    after_sequence = parse_last_event_id(request.headers.get("Last-Event-ID"), after)
    generator = stream_workflow_events(
        session_factory=runtime.session_factory,
        bus=bus,
        workflow_id=workflow_id,
        after_sequence=after_sequence,
        heartbeat_seconds=runtime.settings.sse_heartbeat_seconds,
    )
    return StreamingResponse(
        _with_open_handshake(generator),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _with_open_handshake(frames: AsyncIterator[str]) -> AsyncIterator[str]:
    yield ": connected\n\n"
    async for frame in frames:
        yield frame
