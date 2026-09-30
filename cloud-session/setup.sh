#!/usr/bin/env bash
# Cloud-session setup for Claude Code on the web (cloud sessions ONLY).
#
# The cloud session runs this itself as its first action, from the repo root:
#     bash cloud-session/setup.sh
#
# On any machine that is not a Claude Code cloud session (CLAUDE_CODE_REMOTE is
# never "true" locally) it exits immediately and changes nothing, so the owner's
# local sessions are never affected. `--cloud` forces it, e.g. when it is pasted
# into a cloud environment's "Setup script" field.
set -uo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ] && [ "${1:-}" != "--cloud" ]; then
  echo "cloud-session/setup.sh: not a cloud session (CLAUDE_CODE_REMOTE != true); nothing to do."
  exit 0
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

# 1. Skills, agents and routing rules -> .claude/ in the repo AND the cloud VM's own
#    ~/.claude/ (read at launch whatever the working directory). Both exist only on the
#    cloud VM; the owner's machine never runs this.
mkdir -p .claude/skills .claude/agents
cp -R cloud-session/claude/skills/. .claude/skills/
cp cloud-session/claude/agents/*.md .claude/agents/
cp cloud-session/claude/CLAUDE.md .claude/CLAUDE.md
VMHOME="${HOME:-/root}"
mkdir -p "$VMHOME/.claude/skills" "$VMHOME/.claude/agents"
cp -R cloud-session/claude/skills/. "$VMHOME/.claude/skills/"
cp cloud-session/claude/agents/*.md "$VMHOME/.claude/agents/"
cp cloud-session/claude/CLAUDE.md "$VMHOME/.claude/CLAUDE.md"
echo "cloud-session/setup.sh: agents in $VMHOME/.claude/agents: $(ls "$VMHOME/.claude/agents" | tr '\n' ' ')"

# Hide the copies (and the venv) from git on this machine only; never committed.
EXCLUDE="$(git rev-parse --git-path info/exclude)"
mkdir -p "$(dirname "$EXCLUDE")"
for p in "/.claude/skills/" "/.claude/agents/" "/.claude/CLAUDE.md" "/.venv/"; do
  grep -qxF "$p" "$EXCLUDE" 2>/dev/null || echo "$p" >> "$EXCLUDE"
done
echo "cloud-session/setup.sh: installed $(ls .claude/agents | wc -l) agents and $(ls -d .claude/skills/*/ | wc -l) skills into .claude/."

# 2. Python (>=3.11) virtualenv with the dev extras (skipped if already present).
PYBIN="$(command -v python3.12 || command -v python3.11 || command -v python3)"
[ -x .venv/bin/python ] || "$PYBIN" -m venv .venv
.venv/bin/python -c "import deep_research" 2>/dev/null \
  || { .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -e ".[dev]"; } \
  || echo "WARNING: pip install failed — install manually: .venv/bin/pip install -e '.[dev]'"

# 3. Web dependencies and the Playwright browser (skipped if already present).
if [ ! -d web/node_modules ]; then
  (cd web && npm ci --no-audit --no-fund) || echo "WARNING: npm ci failed — run it manually in web/"
fi
(cd web && { npx playwright install --with-deps chromium || npx playwright install chromium; }) \
  || echo "WARNING: playwright browser install failed — run it manually in web/"

echo "cloud-session/setup.sh: done."
echo "  Python: PY=\"$ROOT/.venv/bin/python\"   Playwright: export DEEP_RESEARCH_PYTHON=\"\$PY\""
exit 0
