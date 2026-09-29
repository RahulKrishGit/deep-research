# Cloud session rules (this file exists only in Claude Code cloud sessions)

`cloud-session/setup.sh` installs this file, the agents and the superpowers skills into `.claude/` on the cloud machine only. Nothing here applies to the owner's local sessions.

## Model routing

Always pass `subagent_type`. Never pass a `model` override.

| Work | Agent | Model | Effort |
|---|---|---|---|
| Implementation: each plan task, and fixes to it | `cloud-implementer` | Sonnet 5.5 | xhigh |
| Per-task review (spec compliance + code quality) and scoped re-reviews | `cloud-task-reviewer` | Opus 5.5 | high |
| Whole-branch / final review | `cloud-branch-reviewer` | Opus 5.5 | max |
| Writing specs and implementation plans | `spec-plan-author` | Opus 5.5 | max |
| Reviewing specs and implementation plans | `spec-plan-reviewer` | Fable 5.1 | max |

These rules override the superpowers skills' own model-selection advice. When `subagent-driven-development` says:
- "dispatch an implementer with model X", use `cloud-implementer`;
- "dispatch a reviewer or re-reviewer", use `cloud-task-reviewer`;
- "dispatch the final code reviewer", use `cloud-branch-reviewer`.

When `writing-plans` or `brainstorming` would plan, use `spec-plan-author`, and have `spec-plan-reviewer` review the result.

## Working rules

- **Review loop:** review → fix → re-review until clean. Never skip a re-review. Use no external or GPT models.
- **Push as you go:** after every commit, `git push` the working branch. Never push to `main`, never force-push, never skip hooks, never open or merge a PR unless asked.
- **Secrets:** never read, create, print or commit `.env` or any `.env.*` file, and never run live or paid model calls. Tests run with pytest and the API in `--mode replay`.
- **Handoffs:** follow the handoff document named in the first prompt exactly, including its stop point.
