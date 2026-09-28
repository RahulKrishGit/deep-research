"""Start command for the local API: ``python -m deep_research.api``.

``--mode live`` serves ``create_app()`` exactly as the module-level ``app``.
``--mode replay`` serves the same app around a ``ReplayRunner`` — the real
graph on scripted offline cases — with the e2e harness's two guards held for
the whole server process: the strict configuration load runs before the
runner (so the placeholder credentials must already be in place), and both
guards patch process globals that overlapping per-run scopes would corrupt.
"""

from __future__ import annotations

import argparse
import shutil
import socket
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from deep_research.api.app import create_app

DEFAULT_REPLAY_CASE = "missing-target-triggers-one-extra-pass"
DEFAULT_REPLAY_DELAY_MS = 150


def _port(value: str) -> int:
    port = int(value)
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m deep_research.api",
        description="Serve the Deep Research API, live or on offline replay cases.",
    )
    parser.add_argument("--mode", choices=("live", "replay"), default="live",
                        help="live: the real graph (needs the configured secrets); replay: scripted "
                             "offline cases, no network (default: %(default)s)")
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind (default: %(default)s)")
    parser.add_argument("--port", type=_port, default=8000, help="port to bind (default: %(default)s)")
    parser.add_argument("--replay-case", metavar="ID", default=None,
                        help=f"replay mode only; default {DEFAULT_REPLAY_CASE}")
    parser.add_argument("--replay-delay-ms", metavar="N", type=int, default=None,
                        help=f"replay mode only; milliseconds between released events, default {DEFAULT_REPLAY_DELAY_MS}; 0 releases as they arrive")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.mode == "live":
        if args.replay_case is not None or args.replay_delay_ms is not None:
            parser.error("--replay-case and --replay-delay-ms are valid only with --mode replay")
        return args
    from deep_research.e2e_evaluation.replay_matrix import REPLAY_CASE_IDS

    if args.replay_case is None:
        args.replay_case = DEFAULT_REPLAY_CASE
    if args.replay_case not in REPLAY_CASE_IDS:
        parser.error(f"unknown replay case {args.replay_case!r}; known: {', '.join(REPLAY_CASE_IDS)}")
    if args.replay_delay_ms is None:
        args.replay_delay_ms = DEFAULT_REPLAY_DELAY_MS
    if args.replay_delay_ms < 0:
        parser.error("--replay-delay-ms must be 0 or more")
    return args


def build_app(args: argparse.Namespace, *, replay_root: Path | None = None) -> FastAPI:
    """One app for either mode; replay mode needs the root ``main`` created."""
    if args.mode == "live":
        return create_app()
    if replay_root is None:
        raise ValueError("replay mode needs a replay_root")
    from deep_research.api.replay import ReplayCaseMiddleware, ReplayRunner
    from deep_research.e2e_evaluation.replay import production_config_path

    runner = ReplayRunner(default_case=args.replay_case, delay=args.replay_delay_ms / 1000, root=replay_root)
    app = create_app(runner=runner, config_path=str(production_config_path()), mode="replay")
    app.add_middleware(ReplayCaseMiddleware, default_case=args.replay_case)
    return app


def main(argv: Sequence[str] | None = None, *, serve: Callable[..., None] = uvicorn.run) -> int:
    args = parse_args(argv)
    if args.mode == "live":
        serve(build_app(args), host=args.host, port=args.port, log_level="info", timeout_graceful_shutdown=5)
        return 0
    from deep_research.e2e_evaluation.replay import network_denied, offline_credentials

    root = Path(tempfile.mkdtemp(prefix="deep-research-replay-"))
    try:
        # Resolve before the guard: network_denied() refuses getaddrinfo, and a numeric host needs
        # none. M4: family=AF_INET — otherwise a resolver that prefers IPv6 for "localhost" binds
        # only [::1], which the default DEEP_RESEARCH_API_URL (127.0.0.1) cannot reach.
        numeric_host = socket.getaddrinfo(args.host, args.port, family=socket.AF_INET, type=socket.SOCK_STREAM)[0][4][0]
        app = build_app(args, replay_root=root)
        print(
            f"deep-research api: mode=replay case={args.replay_case} "
            f"delay_ms={args.replay_delay_ms} root={root}",
            flush=True,
        )
        with offline_credentials(), network_denied():
            serve(app, host=numeric_host, port=args.port, log_level="info", timeout_graceful_shutdown=5)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
