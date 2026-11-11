# FirmPayments

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues for this repo (via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default canonical labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Delivery pipeline

This repo uses [agent-orchestration](https://github.com/TeleginS/agent-orchestration), vendored as the `agent-orchestration/` submodule. Start a task with `/orchestrator <task>`. Add `Mode: dry-run` to keep it off the tracker and the remote.

- Project profile: `.agents/profile.md` (stack, commands, invariants, human gates).
- Role skills: `.agents/skills/<role>` (links into the library), with Claude launchers in `.claude/skills/<role>`.
- Gates: `.claude/hooks/` (configured in `gates.json`, wired in `.claude/settings.json`) and `.githooks/pre-push`.
- Glossary naming: the library's conventions call the domain glossary `CONTEXT.md`. Here it is `GLOSSARY.md`, and both names mean the same file. Don't create a second one.
