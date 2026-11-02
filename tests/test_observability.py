"""Session id must land on every span, not only the agent span."""

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent.observability import SessionSpanProcessor, trace_session


def test_session_id_is_stamped_on_child_spans() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SessionSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    with (
        trace_session("session-abc"),
        tracer.start_as_current_span("outer"),
        tracer.start_as_current_span("inner"),
    ):
        pass

    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["inner", "outer"]
    assert {span.attributes["session.id"] for span in spans} == {"session-abc"}


def test_no_session_attribute_outside_context() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SessionSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("plain"):
        pass

    assert "session.id" not in exporter.get_finished_spans()[0].attributes
