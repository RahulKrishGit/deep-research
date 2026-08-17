"""Entry point for ``python -m deep_research.evaluation``."""

from deep_research.evaluation.cli import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
