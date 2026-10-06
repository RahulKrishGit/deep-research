"""Re-pin one agent's prompt fingerprint.

    python tests/pin_fingerprint.py --show
    python tests/pin_fingerprint.py <agent>

``--show`` prints every agent's pinned value. A re-pin reads the agent's value
as it stands in ``tests/test_evaluation/test_config.py``, computes the
fingerprint of the current source, rewrites the entry and prints
"<agent>: <old> -> <new>". It refuses when the value would not move: a re-pin
follows an edit to that agent's module.

Run from the repository root with ``PYTHONPATH=src``. Not collected by pytest:
the filename does not match ``test_*.py``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from deep_research.evaluation.config import agent_prompt_fingerprint

PINS = Path("tests/test_evaluation/test_config.py")
AGENTS = ("planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer")


def _entry(text: str, agent: str) -> re.Match[str]:
    matches = list(re.finditer(rf'^    "{agent}": "([0-9a-f]{{12}})",$', text, re.M))
    if len(matches) != 1:
        raise SystemExit(f"{agent}: expected one pinned entry, found {len(matches)}")
    return matches[0]


def main(argv: list[str]) -> int:
    text = PINS.read_text(encoding="utf-8")
    if argv == ["--show"]:
        for agent in AGENTS:
            print(agent, _entry(text, agent).group(1))
        return 0
    if len(argv) != 1 or argv[0] not in AGENTS:
        raise SystemExit("usage: pin_fingerprint.py --show | <agent>")
    agent = argv[0]
    match = _entry(text, agent)
    old, new = match.group(1), agent_prompt_fingerprint(agent)
    if old == new:
        raise SystemExit(f"{agent}: the pin did not move ({old})")
    entry = f'    "{agent}": "{new}",'
    PINS.write_text(text[: match.start()] + entry + text[match.end():], encoding="utf-8")
    print(f"{agent}: {old} -> {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
