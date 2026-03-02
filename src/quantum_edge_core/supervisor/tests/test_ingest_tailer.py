from pathlib import Path

from quantum_edge_core.supervisor.supervisor.ingest.tailer import FileTailer


def test_tailer_reads_new_lines(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"type":"ping"}\n', encoding="utf-8")

    tailer = FileTailer(path, max_line_kb=1)
    first = tailer.read_new_lines(0)
    assert len(first.lines) == 1
    assert first.offset > 0

    path.write_text(
        path.read_text(encoding="utf-8") + '{"type":"pong"}\n', encoding="utf-8"
    )
    second = tailer.read_new_lines(first.offset)
    assert len(second.lines) == 1
