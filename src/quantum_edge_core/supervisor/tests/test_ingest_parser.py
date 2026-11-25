from quantum_edge_core.supervisor.supervisor.ingest.parsers import (
    event_hash,
    event_to_point,
    parse_event_line,
)


def test_event_parser_and_point():
    line = '{"type":"ml_gate","ts_utc":"2025-01-01T00:00:00Z","symbol":"BTCUSDT","mode":"demo","component":"bot","data":{"reason":"OK"}}'
    payload = parse_event_line(line)
    assert payload is not None
    digest = event_hash(line)
    point = event_to_point(payload, digest)
    assert point is not None
    assert point.measurement == "qe_events"
