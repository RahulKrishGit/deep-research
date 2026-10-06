"""The start command: flags, composition, and the real server under the guards."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from deep_research.api import __main__ as start
from deep_research.api.replay import ReplayCaseMiddleware, ReplayRunner
from deep_research.e2e_evaluation.replay import production_config_path
from deep_research.e2e_evaluation.replay_matrix import scenario_by_id
from deep_research.main import run_research

ROOT = Path(__file__).resolve().parents[2]


def test_parser_defaults() -> None:
    args = start.parse_args([])
    assert (args.mode, args.host, args.port) == ("live", "127.0.0.1", 8000)
    replay = start.parse_args(["--mode", "replay"])
    assert (replay.replay_case, replay.replay_delay_ms) == (start.DEFAULT_REPLAY_CASE, 150)


@pytest.mark.parametrize(
    "argv",
    [
        ["--mode", "live", "--replay-case", start.DEFAULT_REPLAY_CASE],
        ["--mode", "live", "--replay-delay-ms", "0"],
        ["--mode", "replay", "--replay-case", "no-such-case"],
        ["--mode", "replay", "--replay-delay-ms", "-1"],
        ["--port", "70000"],
        ["--mode", "dry-run"],
    ],
)
def test_bad_flags_exit_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        start.parse_args(argv)
    assert raised.value.code == 2


def test_help_shows_defaults_for_mode_host_and_port(capsys: pytest.CaptureFixture[str]) -> None:
    # argparse only prints defaults if the help string asks.
    with pytest.raises(SystemExit):
        start.parse_args(["--help"])
    out = " ".join(capsys.readouterr().out.split())  # argparse wraps long help lines
    assert "(default: live)" in out
    assert "(default: 127.0.0.1)" in out
    assert "(default: 8000)" in out


def test_build_app_composes_one_app_per_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    live = start.build_app(start.parse_args([]))
    assert live.state.mode == "live"
    assert live.state.session_store._runner is run_research
    assert not any(m.cls is ReplayCaseMiddleware for m in live.user_middleware)
    with pytest.raises(ValueError):
        start.build_app(start.parse_args(["--mode", "replay"]))

    create_app_kwargs: list[dict[str, Any]] = []
    real_create_app = start.create_app

    def spy_create_app(*args: Any, **kwargs: Any) -> FastAPI:
        create_app_kwargs.append(kwargs)
        return real_create_app(*args, **kwargs)

    monkeypatch.setattr(start, "create_app", spy_create_app)
    replay = start.build_app(start.parse_args(["--mode", "replay", "--replay-delay-ms", "0"]), replay_root=tmp_path)
    assert replay.state.mode == "replay"
    assert create_app_kwargs[-1]["config_path"] == str(production_config_path())
    runner = replay.state.session_store._runner
    assert isinstance(runner, ReplayRunner)
    assert (runner.default_case, runner.delay, runner.root) == (start.DEFAULT_REPLAY_CASE, 0.0, tmp_path)
    assert any(m.cls is ReplayCaseMiddleware for m in replay.user_middleware)


def test_main_serves_with_the_numeric_host_and_a_bounded_shutdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    roots_seen: list[Path] = []

    def fake_serve(app: Any, **kwargs: Any) -> None:
        calls.append({"app": app, **kwargs})
        if app.state.mode == "replay":
            root = app.state.session_store._runner.root
            assert root.is_dir()  # the root exists while the server is "serving"
            roots_seen.append(root)

    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", None)  # tempfile caches the first answer; make it re-read TEMP
    assert start.main(["--mode", "live", "--port", "8123"], serve=fake_serve) == 0
    assert calls[-1]["host"] == "127.0.0.1" and calls[-1]["port"] == 8123
    assert calls[-1]["timeout_graceful_shutdown"] == 5 and calls[-1]["app"].state.mode == "live"
    assert start.main(["--mode", "replay", "--host", "localhost", "--port", "8124"], serve=fake_serve) == 0
    # family=AF_INET forces IPv4 — "--host localhost" binds 127.0.0.1, never the IPv6 "::1"
    # some machines' resolvers prefer, which the default DEEP_RESEARCH_API_URL cannot reach.
    assert calls[-1]["host"] == "127.0.0.1" and calls[-1]["port"] == 8124
    assert calls[-1]["timeout_graceful_shutdown"] == 5 and calls[-1]["app"].state.mode == "replay"
    assert roots_seen and not roots_seen[-1].exists()  # ... and is removed once main() returns
    assert list(tmp_path.glob("deep-research-replay-*")) == []  # the root was removed


def test_main_falls_back_to_an_unrestricted_lookup_for_an_ipv6_literal_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # family=socket.AF_INET makes "--host localhost" bind 127.0.0.1 even where a resolver
    # prefers IPv6, but, tried alone, it rejects a *literal* IPv6 host like "::1" outright
    # (AF_INET can't reinterpret an IPv6 address). AF_INET must be preferred, not the only
    # attempt.
    calls: list[dict[str, Any]] = []

    def fake_serve(app: Any, **kwargs: Any) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", None)
    assert start.main(["--mode", "replay", "--host", "::1", "--port", "8125"], serve=fake_serve) == 0
    assert calls[-1]["host"] == "::1" and calls[-1]["port"] == 8125


def test_main_maps_an_unresolvable_host_to_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Neither lookup can resolve every possible --host value; that is a usage error (exit 2,
    # argparse's own "error:" message on stderr), never an unhandled socket.gaierror traceback.
    # Monkeypatched (not a real unresolvable hostname) so this stays offline and deterministic.
    def always_fails(*args: Any, **kwargs: Any) -> Any:
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", always_fails)
    calls: list[Any] = []
    with pytest.raises(SystemExit) as raised:
        start.main(["--mode", "replay", "--host", "nonsense.invalid", "--port", "8126"], serve=lambda *a, **k: calls.append((a, k)))
    assert raised.value.code == 2
    assert calls == []  # serve() is never reached


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_replay_server_serves_a_paced_session_end_to_end(tmp_path: Path) -> None:
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1", "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    env.pop("DEEPSEEK_API_KEY", None)
    env.pop("TAVILY_API_KEY", None)
    log = (tmp_path / "api.log").open("wb")  # never a PIPE nobody reads: Windows pipe buffers are small
    proc = subprocess.Popen(
        [sys.executable, "-m", "deep_research.api", "--mode", "replay", "--port", str(port), "--replay-delay-ms", "50"],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                ready = httpx.get(f"{base}/research", timeout=2)
                if ready.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            assert time.monotonic() < deadline, "the replay server did not come up"
            time.sleep(0.5)
        assert ready.headers["x-deep-research-mode"] == "replay"
        posted = httpx.post(f"{base}/research", json={"query": "anything"}, timeout=10)
        assert posted.status_code == 202
        assert posted.json()["query"] == scenario_by_id(start.DEFAULT_REPLAY_CASE).question
        session_id = posted.json()["session_id"]
        names: list[str] = []
        status_at_first_frame = None
        with httpx.Client(timeout=60) as client, client.stream("GET", f"{base}/research/{session_id}/stream") as stream:
            for line in stream.iter_lines():
                if line.startswith("event: "):
                    names.append(line[7:])
                    if status_at_first_frame is None:
                        status_at_first_frame = client.get(f"{base}/research/{session_id}/status").json()["status"]
        assert status_at_first_frame == "running"
        assert names[0] == "graph.session.started" and names[-1] == "graph.session.completed"
        deadline = time.monotonic() + 10
        while httpx.get(f"{base}/research/{session_id}/status").json()["status"] == "running":
            assert time.monotonic() < deadline
            time.sleep(0.1)
        assert httpx.get(f"{base}/research/{session_id}/status").json()["status"] == "completed"
        assert httpx.get(f"{base}/research/{session_id}/evidence").status_code == 200
    finally:
        proc.terminate()
        proc.wait(timeout=20)
        log.close()
    roots = list(tmp_path.glob("deep-research-replay-*"))
    assert len(roots) <= 1, roots  # a forced kill skips the cleanup; the root landed in tmp_path, not %TEMP%
