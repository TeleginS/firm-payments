---
name: save-session-log
description: Save or refresh the current Claude Code session as a raw Markdown log in docs/ai-logs/ with the bundled converter script. Use this whenever the user asks to save, store, export, add or update the session log, AI log, transcript or conversation, and before committing or opening a PR when the log of the current session is missing or stale. Always use it instead of writing a log by hand.
argument-hint: "[slug for a new log, e.g. implementation-session]"
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/export_log.py *)
---

# Save the session log

The brief for this project asks for the **unedited** logs of every AI conversation: "Don't
clean them up or summarize." So a log in `docs/ai-logs/` is only ever produced by the
script in this skill, straight from the session transcript on disk. Never type, paraphrase,
shorten or hand-edit a log, and never paste its content into the chat: the transcript
includes material that must not be reproduced, such as the copyrighted assignment brief.

## Run it

From the project root:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/export_log.py --session "${CLAUDE_SESSION_ID}" --slug <slug>
```

- **A log for this session already exists**: the script finds it by its `Started`
  timestamp and refreshes it in place. `--slug` is ignored, so you can leave it out.
- **No log yet**: the script creates `docs/ai-logs/NN-<slug>.md` with the next free
  number. If the user passed arguments to this skill, they appear at the end of these
  instructions; use them as the slug. Otherwise name what the session did in two or three lower-case words joined by hyphens,
  for example `implementation-session` or `review-fixes`. Only ask when you can't tell.

The desktop app sometimes resumes a conversation under a new session id. When the id has
no transcript file, the script falls back to the newest transcript in the project, which
is the one being written right now.

## What the script does to the transcript

- Copies user messages, Claude's replies, tool calls and tool results verbatim, in order.
- Leaves out the model's reasoning ("thinking") blocks. The owner chose this.
- Replaces attached documents (the assignment PDF), e-mail addresses and strings that look
  like tokens or private keys with an inline `[REDACTED: ...]` note, and counts them in
  the log's header.
- Writes a header with the tool, model and start time.

The log always ends partway through the turn that ran the export, because the turn isn't
finished yet. The header says so. Run the export as the **last step before a commit** so
as little as possible is missing.

## Read the summary

The script prints one line of JSON. Check it before going further:

- `action` and `file`: tell the user which log was created or refreshed.
- `started`: when refreshing, it should be the start of this conversation. If it isn't,
  the fallback picked another session's transcript. Stop and say so.
- `redacted_documents`, `redacted_emails`, `redacted_secrets`: mention any count that is
  new or surprising. A redacted secret means a real token may have been printed during the
  session, which the user should know about even though the log is clean.

The secret patterns cover common token formats, not every one. If the session handled a
credential in some other form, or something else needs redacting, change the script and
run it again. Don't edit the log.

## Committing

Saving the log doesn't commit it. Commit or push only when the user asks. This repo is
public, so a pushed log can't be taken back.
