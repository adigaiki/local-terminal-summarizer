"""v0.4 session tests: named, explicit, file-backed sessions.

No daemon, no database, no network, no real user home, no model server: the
pipeline runs against a recording engine and every session lives under
``tmp_path``.
"""

from __future__ import annotations

import io
import json
import multiprocessing
from pathlib import Path

import pytest

from summarizer.errors import InputError
from summarizer.log import Diagnostics
from summarizer.pipeline import Pipeline, PipelineOptions
from summarizer.session import (
    ENV_VAR,
    SESSION_SCHEMA,
    SessionManager,
    human_age,
    render_status,
)
from summarizer.session.lock import FileLock
from summarizer.session.run import SessionRun, record_selected, select_session
from test_pipeline_and_cli import RecordingEngine, config
from test_provenance import make_pdf


def quiet() -> Diagnostics:
    return Diagnostics(quiet=True)


def root_for(tmp_path) -> Path:
    return tmp_path / "sessions"


def manager(tmp_path, **kwargs) -> SessionManager:
    kwargs.setdefault("diag", quiet())
    return SessionManager(root=root_for(tmp_path), **kwargs)


def run_once(tmp_path, *, name="note.txt", text="Alpha note. Beta note.", fmt="plain"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return Pipeline(config(), diag=quiet(), engine=RecordingEngine()).run(
        str(path), opts=PipelineOptions(output_format=fmt, stream=False)
    )


def select(tmp_path, /, **kwargs):
    kwargs.setdefault("root", root_for(tmp_path))
    kwargs.setdefault("max_age_hours", 24)
    kwargs.setdefault("digest_format", "markdown")
    kwargs.setdefault("enabled", True)
    kwargs.setdefault("env", {})
    kwargs.setdefault("diag", quiet())
    return select_session(**kwargs)


# --- selection semantics -----------------------------------------------------

def test_explicit_selection_records(tmp_path):
    sessions = manager(tmp_path)
    sessions.start("research")
    result = run_once(tmp_path)
    session = select(tmp_path, explicit="research", cwd=tmp_path)
    assert session is not None and session.name == "research"
    record_selected(session=session, result=result, digest_format="markdown", diag=quiet())
    assert sessions.get("research").runs == 1


def test_explicit_unknown_session_fails_before_any_run(tmp_path):
    with pytest.raises(InputError, match="no such session"):
        select(tmp_path, explicit="ghost")


def test_explicit_closed_session_fails(tmp_path):
    sessions = manager(tmp_path)
    sessions.start("done")
    sessions.end("done")
    with pytest.raises(InputError, match="closed"):
        select(tmp_path, explicit="done")


def test_environment_variable_selection(tmp_path):
    sessions = manager(tmp_path)
    sessions.start("research")
    session = select(tmp_path, env={ENV_VAR: "research"}, cwd=tmp_path)
    assert session is not None and session.name == "research"


def test_no_session_overrides_env_and_explicit(tmp_path):
    sessions = manager(tmp_path)
    sessions.start("research")
    session = select(tmp_path, explicit="research", no_session=True,
                     env={ENV_VAR: "research"}, cwd=tmp_path)
    assert session is None
    assert sessions.get("research").runs == 0


def test_disabled_master_switch_records_nothing(tmp_path):
    sessions = manager(tmp_path)
    sessions.start("research")
    assert select(tmp_path, enabled=False, explicit="research") is None
    assert sessions.get("research").runs == 0


def test_same_directory_rule_is_deterministic(tmp_path):
    sessions = manager(tmp_path)
    sessions.start("research")  # records the current working directory
    session = select(tmp_path, cwd=Path.cwd())
    assert session is not None and session.name == "research"

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert select(tmp_path, cwd=elsewhere) is None  # unrelated directory


def test_ambiguous_directory_records_nothing_and_warns(tmp_path):
    sessions = manager(tmp_path)
    sessions.start("one")
    sessions.start("two")
    stream = io.StringIO()
    session = select(tmp_path, cwd=Path.cwd(), diag=Diagnostics(stream=stream))
    assert session is None  # never guesses
    assert "not recording" in stream.getvalue()


# --- pipeline interactions ---------------------------------------------------

def test_mapreduce_run_records_chunks_and_strategy(tmp_path):
    sessions = manager(tmp_path)
    sessions.start("research")
    path = tmp_path / "long.txt"
    path.write_text("one two three four five six seven eight nine ten " * 30,
                    encoding="utf-8")
    result = Pipeline(config(max_tokens=20), diag=quiet(), engine=RecordingEngine()).run(
        str(path), opts=PipelineOptions(stream=False)
    )
    assert result.aggregation.name == "mapreduce"
    sessions.record("research", SessionRun.from_result(result))
    entry = json.loads(
        (root_for(tmp_path) / "research" / "state.json").read_text(encoding="utf-8")
    )["runs"][0]
    assert entry["strategy"] == "mapreduce"
    assert entry["chunks"] > 1


def test_pdf_run_records_page_provenance(tmp_path):
    sessions = manager(tmp_path)
    sessions.start("research")
    path = tmp_path / "paper.pdf"
    make_pdf(path, ["Page one.", "Page two."])
    result = Pipeline(config(), diag=quiet(), engine=RecordingEngine()).run(
        str(path), opts=PipelineOptions(stream=False)
    )
    sessions.record("research", SessionRun.from_result(result))
    entry = json.loads(
        (root_for(tmp_path) / "research" / "state.json").read_text(encoding="utf-8")
    )["runs"][0]
    assert entry["pages"] == 2
    assert entry["document_type"] == "application/pdf"


def test_json_format_run_stores_pointer_not_envelope(tmp_path):
    sessions = manager(tmp_path)
    sessions.start("research")
    result = run_once(tmp_path, fmt="json")
    sessions.record("research", SessionRun.from_result(result))
    digest = (root_for(tmp_path) / "research" / "digest.md").read_text()
    assert '"prompt_boundary"' not in digest  # no envelope dump
    state = json.loads((root_for(tmp_path) / "research" / "state.json").read_text())
    assert state["runs"][0]["output_format"] == "json"


# --- JSON representation and CLI --------------------------------------------

def test_session_json_representation_is_stable(tmp_path):
    sessions = manager(tmp_path)
    sessions.start("research")
    sessions.record(
        "research",
        SessionRun(source="paper.pdf", profile="academic", extract="Point.",
                   time="2026-09-17T10:00:00+00:00", pages=3, chunks=2,
                   strategy="mapreduce", duration_seconds=1.25),
    )
    payload = sessions.get("research").to_json()
    assert set(payload) >= {
        "name", "id", "state", "created", "last_activity", "closed_at",
        "cwd", "runs", "age_seconds", "age_human", "stale", "directory",
        "digest_path",
    }
    assert payload["runs"] == 1
    assert json.dumps(payload)  # JSON-safe


def test_cli_end_to_end_start_run_status_end(tmp_path, monkeypatch):
    monkeypatch.setattr("summarizer.pipeline.create_engine",
                        lambda *args, **kwargs: RecordingEngine())
    monkeypatch.setenv(ENV_VAR, "research")
    notes = tmp_path / "sessions"
    source = tmp_path / "input.txt"
    source.write_text("Alpha note. Beta note.", encoding="utf-8")

    from summarizer.cli import main

    assert main(["session", "start", "research", "--notes-path", str(notes)]) == 0
    assert main([str(source), "--notes-path", str(notes), "--no-stream", "--quiet"]) == 0
    assert main(["session", "status", "--notes-path", str(notes)]) == 0
    assert main(["session", "end", "--notes-path", str(notes)]) == 0

    digest = notes / "research" / "digest.md"
    assert digest.exists()
    content = digest.read_text(encoding="utf-8")
    assert "input.txt" in content
    assert "Alpha note" not in content  # document text is not stored
    state = json.loads((notes / "research" / "state.json").read_text())
    assert state["state"] == "closed" and len(state["runs"]) == 1


def test_cli_no_session_records_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("summarizer.pipeline.create_engine",
                        lambda *args, **kwargs: RecordingEngine())
    monkeypatch.setenv(ENV_VAR, "research")
    notes = tmp_path / "sessions"
    source = tmp_path / "input.txt"
    source.write_text("Alpha note.", encoding="utf-8")

    from summarizer.cli import main

    assert main(["session", "start", "research", "--notes-path", str(notes)]) == 0
    assert main([str(source), "--no-session", "--notes-path", str(notes),
                 "--no-stream", "--quiet"]) == 0
    state = json.loads(
        (notes / "research" / "state.json").read_text(encoding="utf-8")
    )
    assert state["runs"] == []


def test_cli_duplicate_start_exits_nonzero(tmp_path):
    from summarizer.cli import main

    notes = tmp_path / "sessions"
    assert main(["session", "start", "research", "--notes-path", str(notes)]) == 0
    assert main(["session", "start", "research", "--notes-path", str(notes)]) == 1


def test_cli_end_nonexistent_session_exit_code(tmp_path):
    from summarizer.cli import main

    assert main(["session", "end", "ghost", "--notes-path", str(tmp_path / "s")]) == 1


def test_cli_explicit_session_flag_records(tmp_path, monkeypatch):
    """Regression: `--session NAME` must not be clobbered by operand parsing.

    The env-var path is covered above; the flag goes through argparse and a
    different code path, so it needs its own end-to-end test.
    """
    monkeypatch.setattr("summarizer.pipeline.create_engine",
                        lambda *args, **kwargs: RecordingEngine())
    monkeypatch.delenv(ENV_VAR, raising=False)
    notes = tmp_path / "sessions"
    source = tmp_path / "input.txt"
    source.write_text("Alpha note.", encoding="utf-8")

    from summarizer.cli import main

    assert main(["session", "start", "research", "--notes-path", str(notes)]) == 0
    assert main([str(source), "--session", "research", "--notes-path", str(notes),
                 "--no-stream", "--quiet"]) == 0
    state = json.loads(
        (notes / "research" / "state.json").read_text(encoding="utf-8")
    )
    assert len(state["runs"]) == 1
    assert state["runs"][0]["source"] == str(source)


def test_cli_unknown_session_flag_fails_before_the_run(tmp_path, monkeypatch):
    """A typo in --session must fail fast, not after a model call."""
    calls = []

    class ExplodingEngine(RecordingEngine):
        def generate(self, **kwargs):
            calls.append(kwargs)
            raise AssertionError("the engine must not be called")

    monkeypatch.setattr("summarizer.pipeline.create_engine",
                        lambda *args, **kwargs: ExplodingEngine())
    source = tmp_path / "input.txt"
    source.write_text("Alpha note.", encoding="utf-8")

    from summarizer.cli import main

    code = main([str(source), "--session", "ghost",
                 "--notes-path", str(tmp_path / "sessions"),
                 "--no-stream", "--quiet"])
    assert code == 1
    assert calls == []  # no generation was attempted


def test_cli_session_status_json(tmp_path, capsys):
    from summarizer.cli import main

    notes = tmp_path / "sessions"
    assert main(["session", "start", "research", "--notes-path", str(notes)]) == 0
    capsys.readouterr()  # discard start output
    assert main(["session", "status", "--format", "json",
                 "--notes-path", str(notes)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "summarizer.session.status.v1"
    assert payload["sessions"][0]["name"] == "research"


# --- staleness, atomicity, concurrency --------------------------------------

def test_stale_sessions_warn_but_are_never_closed_or_deleted(tmp_path):
    sessions = manager(tmp_path, max_age_hours=24)
    sessions.start("research")
    state_path = root_for(tmp_path) / "research" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["created"] = "2020-01-01T00:00:00+00:00"  # force an old session
    state_path.write_text(json.dumps(state), encoding="utf-8")

    infos = sessions.status()
    assert infos[0].stale is True
    assert infos[0].state == "active"            # never auto-closed
    assert infos[0].directory.exists()           # never deleted
    text = render_status(infos, max_age_hours=24)
    assert "Warning: session is older than configured max_age_hours" in text
    assert "active" in text


def test_human_age_is_compact_and_readable():
    assert human_age(45) == "45s"
    assert human_age(12 * 60) == "12m"
    assert human_age(5 * 3600 + 22 * 60) == "5h 22m"
    assert human_age(3 * 86400 + 3600) == "3d 1h"


def test_digest_is_rerendered_from_state_after_a_partial_write(tmp_path):
    sessions = manager(tmp_path)
    sessions.start("research")
    digest = root_for(tmp_path) / "research" / "digest.md"
    digest.write_text("CORRUPTED-PARTIAL-WRITE", encoding="utf-8")
    sessions.record(
        "research",
        SessionRun(source="a.txt", profile="plain", extract="e.",
                   time="2026-09-17T10:00:00+00:00"),
    )
    content = digest.read_text(encoding="utf-8")
    assert "CORRUPTED" not in content  # rebuilt from state, atomically
    assert "`a.txt`" in content


def test_interrupted_write_leaves_previous_state_valid(tmp_path, monkeypatch):
    sessions = manager(tmp_path)
    sessions.start("research")
    state_path = root_for(tmp_path) / "research" / "state.json"
    before = state_path.read_text(encoding="utf-8")

    import summarizer.output.atomic as atomic_module

    def explode(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(atomic_module, "atomic_write", explode)
    with pytest.raises(KeyboardInterrupt):
        sessions.record(
            "research",
            SessionRun(source="x", profile="p", extract="e", time="2026-09-17T10:00:00+00:00"),
        )
    monkeypatch.undo()
    assert state_path.read_text(encoding="utf-8") == before  # untouched
    json.loads(state_path.read_text(encoding="utf-8"))       # still valid JSON
    sessions.record(
        "research",
        SessionRun(source="y", profile="p", extract="e.", time="2026-09-17T10:00:00+00:00"),
    )
    assert sessions.get("research").runs == 1  # the next record succeeds


def _concurrent_worker(root, name, index):
    from summarizer.session.run import SessionRun as Run

    SessionManager(root=Path(root), diag=Diagnostics(quiet=True)).record(
        name,
        SessionRun(source=f"doc-{index}.txt", profile="plain",
                   extract=f"run {index}", time="2026-09-17T10:00:00+00:00"),
    )


def test_concurrent_processes_do_not_lose_runs(tmp_path):
    sessions = manager(tmp_path)
    sessions.start("research")
    root = str(root_for(tmp_path))
    processes = [
        multiprocessing.Process(
            target=_concurrent_worker, args=(root, "research", index)
        )
        for index in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    assert sessions.get("research").runs == 4


def test_only_pointers_are_stored_never_content(tmp_path):
    sessions = manager(tmp_path)
    sessions.start("research")
    document_body = "CONFIDENTIAL-DOCUMENT-BODY-never-store"
    result = run_once(tmp_path, name="secret.txt", text=document_body)
    sessions.record("research", SessionRun.from_result(result))

    digest = (root_for(tmp_path) / "research" / "digest.md").read_text()
    state = json.loads((root_for(tmp_path) / "research" / "state.json").read_text())
    for artifact in (digest, json.dumps(state)):
        assert document_body not in artifact   # never the document text
        assert "CONFIDENTIAL" not in artifact
    entry = state["runs"][0]
    assert entry["source"] == str(tmp_path / "secret.txt")  # pointer, not content
    assert len(entry["extract"]) <= 200


# --- lifecycle ---------------------------------------------------------------

def test_start_creates_state_and_digest(tmp_path):
    info = manager(tmp_path).start("research")
    assert info.active
    directory = root_for(tmp_path) / "research"
    assert (directory / "state.json").exists()
    assert (directory / "digest.md").exists()
    state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    assert state["schema"] == SESSION_SCHEMA
    assert state["name"] == "research"
    assert state["state"] == "active"
    assert state["runs"] == []
    assert "No runs recorded yet" in (directory / "digest.md").read_text()


def test_permissions_are_restrictive(tmp_path):
    manager(tmp_path).start("research")
    root = root_for(tmp_path)
    directory = root / "research"
    assert (root.stat().st_mode & 0o777) == 0o700
    assert (directory.stat().st_mode & 0o777) == 0o700
    assert ((directory / "state.json").stat().st_mode & 0o777) == 0o600
    assert ((directory / "digest.md").stat().st_mode & 0o777) == 0o600


def test_status_lists_sessions_with_runs_and_age(tmp_path):
    sessions = manager(tmp_path)
    sessions.start("research")
    sessions.record(
        "research",
        SessionRun(source="paper.pdf", profile="academic", extract="Point.",
                   time="2026-09-17T10:00:00+00:00"),
    )
    infos = sessions.status()
    assert len(infos) == 1
    assert infos[0].name == "research"
    assert infos[0].runs == 1
    assert infos[0].age_seconds >= 0
    text = render_status(infos, max_age_hours=24)
    assert "research" in text and "runs: 1" in text


def test_status_when_no_sessions_exist(tmp_path):
    text = render_status(manager(tmp_path).status(), max_age_hours=24)
    assert "No sessions yet" in text


def test_close_finalizes_digest_and_reports_path(tmp_path):
    sessions = manager(tmp_path)
    sessions.start("research")
    sessions.record(
        "research",
        SessionRun(source="paper.pdf", profile="academic", extract="A finding.",
                   time="2026-09-17T10:00:00+00:00"),
    )
    info = sessions.end("research")
    assert info.state == "closed"
    assert info.runs == 1
    assert info.digest_path and info.digest_path.exists()
    digest = info.digest_path.read_text(encoding="utf-8")
    assert "paper.pdf" in digest and "academic" in digest and "A finding." in digest
    assert "closed" in digest


def test_duplicate_start_fails_without_damaging_state(tmp_path):
    sessions = manager(tmp_path)
    sessions.start("research")
    with pytest.raises(InputError, match="already active"):
        sessions.start("research")
    assert sessions.get("research").state == "active"
    assert sessions.get("research").runs == 0


def test_restarting_a_closed_session_resumes_it(tmp_path):
    sessions = manager(tmp_path)
    sessions.start("research")
    sessions.record(
        "research",
        SessionRun(source="a.txt", profile="plain", extract="a.", time="2026-09-17T10:00:00+00:00"),
    )
    sessions.end("research")
    info = sessions.start("research")
    assert info.active
    assert info.runs == 1  # history is kept, not silently discarded


def test_end_unknown_session_is_a_clear_error(tmp_path):
    with pytest.raises(InputError, match="no such session"):
        manager(tmp_path).end("ghost")


def test_end_without_name_when_nothing_is_active(tmp_path):
    with pytest.raises(InputError, match="no active session"):
        manager(tmp_path).end()


def test_record_into_unknown_session_fails(tmp_path):
    with pytest.raises(InputError, match="not active|no such session"):
        manager(tmp_path).record(
            "ghost",
            SessionRun(source="x", profile="p", extract="e", time="2026-09-17T10:00:00+00:00"),
        )


@pytest.mark.parametrize(
    "bad", ["", "../escape", "a/b", "a b", "-leading", "x" * 65, None, "."]
)
def test_invalid_session_names_are_rejected(tmp_path, bad):
    with pytest.raises(InputError, match="invalid session name"):
        manager(tmp_path).start(bad)


def test_lock_is_exclusive(tmp_path):
    lock = root_for(tmp_path) / "research" / "lock"
    with FileLock(lock):
        with pytest.raises(TimeoutError):
            with FileLock(lock, timeout=0.1):
                pass