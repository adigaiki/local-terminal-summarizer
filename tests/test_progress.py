"""Progress rendering: stderr-only, quiet/TTY aware, never stdout."""

from __future__ import annotations

import io

from summarizer.log import Diagnostics
from summarizer.progress import ProgressReporter, build_progress


class FakeStream(io.StringIO):
    def __init__(self, *, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:  # type: ignore[override]
        return self._tty


def _diag(stream, *, quiet=False) -> Diagnostics:
    return Diagnostics(quiet=quiet, stream=stream)


def test_plain_non_tty_mode_prints_start_and_finish_only():
    stream = FakeStream(tty=False)
    reporter = build_progress(_diag(stream), enabled=True)
    reporter.start(label="note.txt", model="m", total_chunks=5)
    for i in range(1, 6):
        reporter.update(i)
    reporter.finish(elapsed=1.0)

    text = stream.getvalue()
    assert "Summarizing note.txt" in text
    assert "5/5 chunks" in text
    # No interactive control sequences, and no per-chunk spam.
    assert "\r" not in text
    assert text.count("\n") == 2


def test_interactive_tty_mode_draws_and_clears_a_bar():
    stream = FakeStream(tty=True)
    reporter = build_progress(_diag(stream), enabled=True)
    reporter.start(label="paper.pdf", model="qwen3:8b", total_chunks=10)
    for i in range(1, 11):
        reporter.update(i)
    reporter.finish(elapsed=31.0)

    text = stream.getvalue()
    assert "\r" in text  # in-place updates
    assert "\u2588" in text  # a filled block
    assert "qwen3:8b" in text
    assert "elapsed: 31s" in text
    assert "10/10 chunks done" in text


def test_quiet_disables_progress_entirely():
    stream = FakeStream(tty=True)
    reporter = build_progress(_diag(stream, quiet=True), enabled=True)
    reporter.start(label="x", model="m", total_chunks=3)
    reporter.update(1)
    reporter.finish()
    assert stream.getvalue() == ""


def test_enabled_false_disables_progress():
    stream = FakeStream(tty=True)
    reporter = build_progress(_diag(stream), enabled=False)
    reporter.start(label="x", model="m", total_chunks=3)
    reporter.update(1)
    reporter.finish()
    assert stream.getvalue() == ""


def test_note_is_a_single_line_even_interactively():
    stream = FakeStream(tty=True)
    reporter = build_progress(_diag(stream), enabled=True)
    reporter.start(label="x", model="m", total_chunks=4)
    reporter.note("cache: reusing 2/4 chunk summaries")
    assert "cache: reusing 2/4 chunk summaries" in stream.getvalue()


def test_progress_never_touches_stdout(monkeypatch, tmp_path):
    """The reporter writes only to its own stream, never to sys.stdout."""
    import sys

    written: list[str] = []

    class GuardedStdout(io.StringIO):
        def write(self, text):  # type: ignore[override]
            written.append(text)
            return super().write(text)

    monkeypatch.setattr(sys, "stdout", GuardedStdout())
    stream = FakeStream(tty=True)
    reporter = build_progress(_diag(stream), enabled=True)
    reporter.start(label="x", model="m", total_chunks=2)
    reporter.update(1)
    reporter.finish(elapsed=0.5)
    assert written == []


def test_reporter_survives_a_broken_stream():
    class Broken(io.StringIO):
        def write(self, text):  # type: ignore[override]
            raise OSError("stderr closed")

    reporter = ProgressReporter(diag=_diag(Broken()), enabled=True)
    reporter.start(label="x", model="m", total_chunks=1)  # must not raise
    reporter.update(1)
    reporter.finish()
    assert reporter.enabled is False
