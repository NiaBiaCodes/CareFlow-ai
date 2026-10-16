from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from worker.telemetry import Telemetry


class Exporter(SpanExporter):
    def __init__(self, fail=False): self.spans, self.fail = [], fail
    def export(self, spans):
        self.spans.extend(spans)
        return SpanExportResult.FAILURE if self.fail else SpanExportResult.SUCCESS
    def shutdown(self): pass


def test_worker_trace_parent_and_validation_result():
    app_exporter, worker_exporter = Exporter(), Exporter()
    app = Telemetry("task-id", exporter=app_exporter)
    with app.span("application"):
        worker = Telemetry("task-id", parent=app.carrier(), exporter=worker_exporter)
        with worker.span("worker.model"): pass
        with app.span("validation", **{"careflow.validation_result": "blocked"}): pass
    assert worker.finish()["status"] == "exported"
    assert app.finish()["status"] == "exported"
    assert worker_exporter.spans[0].context.trace_id == app_exporter.spans[-1].context.trace_id
    assert app_exporter.spans[0].attributes["careflow.validation_result"] == "blocked"


def test_export_failures_are_explicit_and_dont_raise():
    telemetry = Telemetry("task-id", exporter=Exporter(fail=True))
    with telemetry.span("completed_work"): pass
    assert telemetry.finish()["status"] == "export_failed"


def test_cleanup_after_operation_keeps_original_trace_and_parent():
    exporter = Exporter()
    telemetry = Telemetry("task-id", exporter=exporter)
    with telemetry.span("submission") as root:
        carrier = telemetry.carrier()
    with telemetry.span("cleanup") as cleanup:
        assert cleanup.get_span_context().trace_id == root.get_span_context().trace_id
    report = telemetry.finish()
    assert report["trace_id"] == carrier["traceparent"].split("-")[1]
    assert exporter.spans[-1].parent.span_id == root.get_span_context().span_id
