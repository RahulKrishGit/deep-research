"""Command-line entry point for the whole-report quality campaign."""

from __future__ import annotations

from deep_research.e2e_evaluation.runner import main

if __name__ == "__main__":  # pragma: no cover - exercised by subprocesses
    raise SystemExit(main())
