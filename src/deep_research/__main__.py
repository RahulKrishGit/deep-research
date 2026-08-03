"""Entry point for ``python -m deep_research``."""

from deep_research.cli import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
