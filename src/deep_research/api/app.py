"""FastAPI app: start research sessions and report their status.

Routes stay thin adapters around the process-local ``SessionStore``; every
request runs inside one API observability span, and every client-visible
failure is a safe structured ``ApiErrorResponse``. Nothing here touches a
provider, the file system, or the network — sessions are memory-only and
cancel cleanly when the application shuts down.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TypeAlias

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse

from deep_research.api.events import api_error_event, encode_sse
from deep_research.api.models import (
    ApiErrorBody,
    ApiErrorResponse,
    ResearchRequest,
    ResearchSessionResponse,
    TraceMetadata,
    TraceResponse,
    ValidationIssue,
)
from deep_research.api.sessions import (
    ResearchRunner,
    ResearchSession,
    SessionStore,
)
from deep_research.main import (
    DEFAULT_CONFIG_PATH,
    new_session_id,
    prepare_research_settings,
    run_research,
)
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.runtime.errors import ResearchConfigurationError
from deep_research.utils.config import ConfigSettings

PreflightHandler: TypeAlias = Callable[..., ConfigSettings]

_SAFE_MESSAGES = {
    "validation_error": "Request validation failed.",
    "session_not_found": "Research session not found.",
    "configuration_error": "Research service configuration is unavailable.",
    "session_not_complete": "Research session has not produced a report yet.",
    "report_unavailable": "Research session finished without a report.",
}
_DEFAULT_ERROR_MESSAGE = "API request failed."


class ApiProblem(Exception):
    """An expected API failure rendered as a safe error response.

    Carries only the enumerated code, the HTTP status, and an optional
    enumerated reason — never an exception message, input value, or secret.
    """

    def __init__(
        self,
        *,
        code: str,
        status_code: int,
        reason: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.reason = reason


async def _trace_request(request: Request) -> AsyncIterator[None]:
    """Bind one API observability span around every route handler.

    POST /research has no path session id, so the dependency invents one
    before the route starts; status lookups take the id from the path. The
    route template (not the concrete path) is recorded so a status URL is
    never mistaken for a report or stream URL in traces.
    """
    session_id = request.path_params.get("session_id") or new_session_id()
    route = request.scope["route"].path
    request.state.session_id = session_id
    request.state.route = route
    tracker: Tracker = request.app.state.api_tracker
    async with tracker.api_request_span(session_id, route, request.method):
        yield


def _record_api_error(
    request: Request,
    *,
    code: str,
    status_code: int,
    reason: str | None = None,
) -> None:
    request.app.state.api_tracker.record_event(
        api_error_event(
            session_id=getattr(request.state, "session_id", None),
            route=getattr(request.state, "route", None),
            method=request.method,
            status_code=status_code,
            code=code,
            reason=reason,
        )
    )


def _session_response(session: ResearchSession) -> ResearchSessionResponse:
    return ResearchSessionResponse(
        session_id=session.session_id,
        status=session.status,
        current_agent=session.current_agent,
        iteration=session.iteration,
        started_at=session.started_at,
        finished_at=session.finished_at,
        report_path=session.report_path,
        trace_url=session.trace_url,
        errors=[error.model_copy(deep=True) for error in session.errors],
    )



def create_app(
    *,
    runner: ResearchRunner = run_research,
    config_path: str = DEFAULT_CONFIG_PATH,
    preflight: PreflightHandler = prepare_research_settings,
    tracker: Tracker | None = None,
) -> FastAPI:
    """Build the local FastAPI interface around one process's session store."""
    if tracker is None:
        tracker = Tracker(
            LangSmithRuntimeConfig(
                tracing_enabled=False,
                project="deep-research-api",
                api_key=None,
            )
        )
    store = SessionStore(runner=runner)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await store.close()

    app = FastAPI(lifespan=lifespan)
    app.state.session_store = store
    app.state.api_tracker = tracker

    router = APIRouter(dependencies=[Depends(_trace_request)])

    @router.post(
        "/research",
        status_code=202,
        response_model=ResearchSessionResponse,
    )
    async def start_research(
        request: Request,
        payload: ResearchRequest,
    ) -> ResearchSessionResponse:
        try:
            preflight(
                config_path=config_path,
                output_format=payload.output_format,
                config_overrides=payload.config_overrides,
            )
        except ResearchConfigurationError as error:
            raise ApiProblem(
                code="configuration_error",
                status_code=500,
                reason=error.reason,
            ) from error
        session = store.start(
            session_id=request.state.session_id,
            query=payload.query,
            max_iterations=payload.max_iterations,
            output_format=payload.output_format,
            config_overrides=payload.config_overrides,
            config_path=config_path,
        )
        return _session_response(session)

    @router.get(
        "/research/{session_id}/status",
        response_model=ResearchSessionResponse,
    )
    async def research_status(request: Request) -> ResearchSessionResponse:
        try:
            session = store.require(request.state.session_id)
        except KeyError:
            raise ApiProblem(
                code="session_not_found",
                status_code=404,
            ) from None
        return _session_response(session)

    @router.get("/research/{session_id}/stream")
    async def research_stream(request: Request) -> StreamingResponse:
        """Stream the session's progress as server-sent events.

        The session must exist *before* the response starts, so an unknown
        id gets the same safe 404 as every other route instead of a failure
        halfway through a stream. Each subscriber replays the retained
        events from id one and then follows live progress until the session
        reaches a terminal state; only enumerated event types, event ids,
        and typed ``ResearchEvent`` JSON leave this route.
        """
        try:
            store.require(request.state.session_id)
        except KeyError:
            raise ApiProblem(
                code="session_not_found",
                status_code=404,
            ) from None

        async def body() -> AsyncIterator[str]:
            event_id = 0
            async for event in store.iter_events(request.state.session_id):
                event_id += 1
                yield encode_sse(event, event_id=event_id)

        return StreamingResponse(
            body(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/research/{session_id}/report")
    async def research_report(request: Request) -> Response:
        """Return the authoritative Markdown report of a finished session.

        A running session has no report yet and a finished session may have
        finished without one, so both are explicit 409 conflicts — never a
        fabricated body and never a fake 404.
        """
        try:
            session = store.require(request.state.session_id)
        except KeyError:
            raise ApiProblem(
                code="session_not_found",
                status_code=404,
            ) from None
        if session.outcome is None:
            raise ApiProblem(
                code="session_not_complete",
                status_code=409,
            )
        if session.outcome.report is None:
            raise ApiProblem(
                code="report_unavailable",
                status_code=409,
            )
        return Response(session.outcome.report, media_type="text/markdown")

    @router.get(
        "/research/{session_id}/trace",
        response_model=TraceResponse,
    )
    async def research_trace(request: Request) -> TraceResponse:
        """Expose the session's trace URL and route metadata.

        Known sessions always answer, running or finished: a running session
        simply has no ``trace_url`` yet. The recorded route is the template,
        not the concrete path, so a trace URL can never be mistaken for a
        stream or report URL.
        """
        try:
            session = store.require(request.state.session_id)
        except KeyError:
            raise ApiProblem(
                code="session_not_found",
                status_code=404,
            ) from None
        return TraceResponse(
            session_id=session.session_id,
            trace_url=session.trace_url,
            metadata=TraceMetadata(
                session_id=session.session_id,
                route=request.state.route,
                status=session.status,
            ),
        )

    app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        issues = [
            ValidationIssue(
                location=".".join(str(part) for part in error["loc"]),
                type=str(error["type"]),
            )
            for error in exc.errors()
        ]
        _record_api_error(
            request,
            code="validation_error",
            status_code=422,
        )
        return JSONResponse(
            status_code=422,
            content=ApiErrorResponse(
                error=ApiErrorBody(
                    code="validation_error",
                    message=_SAFE_MESSAGES["validation_error"],
                    issues=issues,
                )
            ).model_dump(mode="json"),
        )

    @app.exception_handler(ApiProblem)
    async def api_problem_handler(
        request: Request,
        exc: ApiProblem,
    ) -> JSONResponse:
        _record_api_error(
            request,
            code=exc.code,
            status_code=exc.status_code,
            reason=exc.reason,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=ApiErrorResponse(
                error=ApiErrorBody(
                    code=exc.code,
                    message=_SAFE_MESSAGES.get(
                        exc.code, _DEFAULT_ERROR_MESSAGE
                    ),
                    reason=exc.reason,
                )
            ).model_dump(mode="json"),
        )

    return app


app = create_app()
