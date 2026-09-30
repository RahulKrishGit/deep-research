# cloud-session/

This folder is for **Claude Code cloud sessions only**. Local Claude Code never reads it, because it is not under `.claude/`, so local sessions keep their own user-level skills, agents and routing.

Cloud sessions cannot install plugins, and they don't see the owner's `~/.claude/`. `setup.sh` fills that gap on the cloud machine: when `CLAUDE_CODE_REMOTE=true`, it
- copies these files into `.claude/` and hides the copies from git;
- creates `.venv`;
- runs `npm ci` and installs Playwright's chromium.

| Path | What |
|---|---|
| `claude/skills/` | superpowers skills, vendored from github.com/obra/superpowers (MIT; see `SUPERPOWERS-LICENSE`; upstream commit in `SUPERPOWERS-VERSION`) |
| `claude/agents/` | `cloud-implementer` (Sonnet 5.5, xhigh), `cloud-task-reviewer` (Opus 5.5, high), `cloud-branch-reviewer` (Opus 5.5, max), `spec-plan-author` (Opus 5.5, max), `spec-plan-reviewer` (Fable 5.1, max) |
| `claude/CLAUDE.md` | the cloud-only routing and working rules |
| `setup.sh` | run by the cloud session itself as its first action (`bash cloud-session/setup.sh`). It is a no-op unless `CLAUDE_CODE_REMOTE=true`, and never runs locally. Optionally paste `bash cloud-session/setup.sh --cloud` into the cloud environment's "Setup script" field so the dependencies are cached. |

Nothing is placed in `.claude/settings.json`, and no hook is added, so local sessions run nothing from here.
