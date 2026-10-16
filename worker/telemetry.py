"""Phoenix-compatible OTLP traces with observable export failures, no secret logging."""
from contextlib import contextmanager
import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


class CheckedExporter(SpanExporter):
    def __init__(self, exporter):
        self.exporter, self.failed, self.count = exporter, False, 0

    def export(self, spans):
        try:
            result = self.exporter.export(spans)
            self.failed |= result != SpanExportResult.SUCCESS
            self.count += len(spans) if result == SpanExportResult.SUCCESS else 0
            return result
        except Exception:
            self.failed = True
            return SpanExportResult.FAILURE

    def shutdown(self):
        self.exporter.shutdown()


class Telemetry:
    def __init__(self, task_id, endpoint="", api_key="", parent=None, exporter=None):
        self.task_id = task_id
        self.provider = TracerProvider(resource=Resource.create({
            "service.name": "careflow", "openinference.project.name": "careflow-demo"}))
        self.checked = None
        self.configuration_error = False
        self.parent = TraceContextTextMapPropagator().extract(parent or {})
        self.trace_id = None
        self.root_context = None
        if endpoint or exporter is not None:
            try:
                if exporter is None:
                    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                    exporter = OTLPSpanExporter(endpoint=endpoint, timeout=4,
                        certificate_file=os.getenv("SSL_CERT_FILE") or None,
                        headers={"authorization": "Bearer " + api_key} if api_key else {})
                self.checked = CheckedExporter(exporter)
                self.provider.add_span_processor(SimpleSpanProcessor(self.checked))
            except Exception:
                self.configuration_error = True
        self.tracer = self.provider.get_tracer("careflow.demo")

    @contextmanager
    def span(self, name, **attributes):
        # Propagate the remote parent only for roots; children use the current span.
        parent = (self.root_context or self.parent) if not trace.get_current_span().get_span_context().is_valid else None
        with self.tracer.start_as_current_span(name, context=parent,
                record_exception=False, set_status_on_exception=False) as span:
            if self.trace_id is None:
                self.trace_id = format(span.get_span_context().trace_id, "032x")
                self.root_context = trace.set_span_in_context(span)
            span.set_attribute("careflow.task_id", self.task_id)
            span.set_attribute("openinference.span.kind", "CHAIN")
            for key, value in attributes.items(): span.set_attribute(key, value)
            try:
                yield span
            except Exception as exc:
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                span.set_attribute("careflow.execution_error", type(exc).__name__)
                raise

    def carrier(self):
        carrier = {}
        TraceContextTextMapPropagator().inject(carrier)
        return carrier

    def finish(self):
        try: flushed = self.provider.force_flush(timeout_millis=5000)
        except Exception: flushed = False
        status = "not_configured" if self.checked is None else (
            "export_failed" if self.checked.failed or not flushed else "exported")
        if self.configuration_error: status = "export_failed"
        try: self.provider.shutdown()
        except Exception: status = "export_failed"
        return {"status": status, "trace_id": self.trace_id,
                "exported_spans": self.checked.count if self.checked else 0}

    def error(self, exc):
        span = trace.get_current_span()
        span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
        span.set_attribute("careflow.execution_error", type(exc).__name__)
