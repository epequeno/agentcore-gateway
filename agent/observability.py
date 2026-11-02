"""OpenTelemetry setup shared by the Runtime and local eval runs.

Strands already emits spans under ``strands.telemetry.tracer``; this module
makes sure a real tracer provider exists and that a session id lands on every
span, then lets a local run fan the same spans out to a second sink.

Deployed (AgentCore Runtime) the container starts under
``opentelemetry-instrument`` with ADOT, which installs the global provider and
exports to CloudWatch (unified telemetry: the agent's own log group). Nothing
here is needed for that path beyond the session attribute.

Locally, ``PHOENIX_OTLP_ENDPOINT`` adds an OTLP/HTTP batch processor pointing at
the shared observability stack, so the same spans can be scored in Phoenix.
"""

import atexit
import logging
import os

from opentelemetry import baggage, context, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

DEFAULT_SERVICE_NAME = "agentcore-gateway"
DEFAULT_PROJECT_NAME = "agentcore-gateway"
SESSION_BAGGAGE_KEY = "session.id"

_configured = False


class SessionSpanProcessor(SpanProcessor):
    """Copy the ``session.id`` baggage entry onto every span.

    AgentCore Runtime injects the attribute itself when the request carries the
    runtime session header. Local runs have no such header, so the baggage
    processor keeps the same span shape for the Phoenix comparison.
    """

    def on_start(self, span, parent_context=None) -> None:  # type: ignore[override]
        session_id = baggage.get_baggage(SESSION_BAGGAGE_KEY, parent_context)
        if session_id:
            span.set_attribute(SESSION_BAGGAGE_KEY, str(session_id))


def _traces_endpoint(endpoint: str) -> str:
    """Accept a base OTLP URL or a full ``/v1/traces`` URL."""
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/v1/traces"):
        return endpoint
    return f"{endpoint}/v1/traces"


def _shutdown(provider: TracerProvider) -> None:
    """Flush pending spans; a batch processor otherwise loses the last batch."""
    try:
        provider.shutdown()
    except Exception as exc:  # pragma: no cover - shutdown is best effort
        logger.warning("Tracer provider shutdown failed: %s", exc)


def configure_telemetry() -> TracerProvider:
    """Ensure a tracer provider exists and add the project's span processors.

    Idempotent: repeated calls (imports, tests) do not stack processors.
    """
    global _configured

    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": os.environ.get("OTEL_SERVICE_NAME", DEFAULT_SERVICE_NAME),
                    "openinference.project.name": os.environ.get(
                        "PHOENIX_PROJECT_NAME", DEFAULT_PROJECT_NAME
                    ),
                }
            )
        )
        trace.set_tracer_provider(provider)

    if _configured:
        return provider

    provider.add_span_processor(SessionSpanProcessor())

    endpoint = os.environ.get("PHOENIX_OTLP_ENDPOINT", "").strip()
    if endpoint:
        # Imported lazily: deployments that only use ADOT should not need the
        # HTTP exporter installed in the image.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=_traces_endpoint(endpoint)))
        )
        logger.info("Phoenix OTLP export enabled at %s", _traces_endpoint(endpoint))

    atexit.register(_shutdown, provider)
    _configured = True
    return provider


def trace_session(session_id: str):
    """Context manager that stamps ``session.id`` onto spans started inside it."""
    return _SessionContext(session_id)


class _SessionContext:
    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._token = None

    def __enter__(self) -> "_SessionContext":
        ctx = baggage.set_baggage(SESSION_BAGGAGE_KEY, self._session_id)
        self._token = context.attach(ctx)
        return self

    def __exit__(self, *exc_info) -> None:
        if self._token is not None:
            context.detach(self._token)
