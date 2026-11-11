#!/usr/bin/env python3
"""Save a Claude Code session transcript as a raw Markdown log.

The brief asks for unedited AI logs, so this script copies the transcript
verbatim and nothing else writes to docs/ai-logs/. It leaves out the model's
reasoning ("thinking") blocks and redacts, with an inline note, attached
documents (the assignment PDF), e-mail addresses and strings that look like
tokens or private keys.

Usage:
    export_log.py [--session ID] [--slug NAME] [--out-dir DIR]
                  [--project-dir DIR] [--transcript FILE]

If a log for the same session already exists in the output directory (same
"Started" timestamp), it is refreshed in place. Otherwise a new file
NN-<slug>.md is created with the next free number.

Prints a one-line JSON summary. Exit codes: 0 ok, 2 usage error or no
transcript.
"""

import argparse
import json
import re
import sys
from pathlib import Path

PDF_NOTE = (
    "[REDACTED: content of the TaxDome assignment PDF, which is copyrighted "
    "and kept out of this public repo]"
)
EMAIL_NOTE = "[REDACTED: e-mail address]"
SECRET_NOTE = "[REDACTED: secret-like string]"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# Addresses that are not personal data and must stay readable.
KEEP_EMAILS = {"noreply@anthropic.com", "git@github.com"}
SECRET_RE = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|\bsk-[A-Za-z0-9_-]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|Bearer [A-Za-z0-9._-]{20,}"
    r"|BEGIN [A-Z ]*PRIVATE KEY"
)
ENTRYPOINTS = {"claude-desktop": "desktop app", "cli": "CLI"}

counts = {"pdf": 0, "email": 0, "secret": 0, "thinking": 0}


def fail(message: str):
    print(message, file=sys.stderr)
    sys.exit(2)


def scrub(text: str) -> str:
    def secret(match):
        counts["secret"] += 1
        return SECRET_NOTE

    def email(match):
        if match.group(0) in KEEP_EMAILS:
            return match.group(0)
        counts["email"] += 1
        return EMAIL_NOTE

    return EMAIL_RE.sub(email, SECRET_RE.sub(secret, text))


def fence(text: str, lang: str = "") -> str:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{lang}\n{text}\n{ticks}"


def result_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        kind = block.get("type")
        if kind == "text":
            parts.append(block.get("text", ""))
        elif kind == "document":
            counts["pdf"] += 1
            parts.append(PDF_NOTE)
        elif kind == "tool_reference":
            parts.append(f"[tool reference: {block.get('tool_name', '')}]")
        else:
            parts.append(f"[{kind} block omitted]")
    return "\n".join(parts)


def find_transcript(args) -> Path:
    if args.transcript:
        return Path(args.transcript)
    project_dir = Path(args.project_dir).resolve()
    store = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(project_dir))
    if args.session:
        candidate = store / f"{args.session}.jsonl"
        if candidate.is_file():
            return candidate
    # The desktop app can resume a conversation under a new session id, so the
    # id we were given may not have a file. The transcript being written right
    # now is the newest one.
    transcripts = sorted(store.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not transcripts:
        fail(f"no transcripts found in {store}")
    return transcripts[-1]


def load_rows(path: Path) -> list:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("type") in ("user", "assistant") and not row.get("isSidechain"):
                rows.append(row)
    return rows


def render_body(rows: list) -> list:
    results = {}
    for row in rows:
        content = row["message"].get("content")
        if row["type"] == "user" and isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_result":
                    results[block["tool_use_id"]] = result_text(block.get("content"))

    body = []
    in_claude = False
    for row in rows:
        ts = row.get("timestamp", "")
        content = row["message"].get("content")
        if row["type"] == "user":
            if isinstance(content, str):
                texts = [content]
            else:
                texts = [b.get("text", "") for b in content if b.get("type") == "text"]
            for text in texts:
                body.append(f"## 🧑 User · {ts}\n\n{scrub(text)}\n")
                in_claude = False
            continue

        for block in content:
            kind = block.get("type")
            if kind in ("thinking", "redacted_thinking"):
                counts["thinking"] += 1
                continue
            if not in_claude:
                body.append(f"## 🤖 Claude · {ts}\n")
                in_claude = True
            if kind == "text":
                body.append(f"{scrub(block.get('text', ''))}\n")
            elif kind == "tool_use":
                call = json.dumps(block.get("input", {}), indent=2, ensure_ascii=False)
                body.append(f"**🔧 Tool call: `{block.get('name')}`**\n\n{fence(scrub(call), 'json')}\n")
                if block["id"] in results:
                    out = scrub(results[block["id"]])
                    body.append(f"<details><summary>Tool result</summary>\n\n{fence(out)}\n\n</details>\n")
    return body


def choose_target(out_dir: Path, started: str, slug: str):
    """Return (path, created). Reuse the log of the same session if there is one."""
    marker = f"- Started: {started}"
    highest = -1
    for path in sorted(out_dir.glob("*.md")):
        match = re.match(r"(\d+)-", path.name)
        if match:
            highest = max(highest, int(match.group(1)))
        with path.open(encoding="utf-8") as fh:
            head = [next(fh, "") for _ in range(8)]
        if any(line.strip() == marker for line in head):
            return path, False
    if not slug:
        fail("this session has no log yet: pass --slug, for example --slug implementation-session")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug):
        fail("--slug must be lower-case words joined by hyphens")
    return out_dir / f"{highest + 1:02d}-{slug}.md", True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session", default="", help="session id; falls back to the newest transcript")
    parser.add_argument("--slug", default="", help="name for a new log, e.g. implementation-session")
    parser.add_argument("--out-dir", default="docs/ai-logs")
    parser.add_argument("--project-dir", default=".", help="project root the session ran in")
    parser.add_argument("--transcript", default="", help="explicit path to a .jsonl transcript")
    args = parser.parse_args()

    transcript = find_transcript(args)
    if not transcript.is_file():
        fail(f"transcript not found: {transcript}")
    rows = load_rows(transcript)
    if not rows:
        fail(f"no conversation in {transcript}")

    model = next(
        (r["message"].get("model") for r in rows if r["type"] == "assistant" and r["message"].get("model")),
        "unknown",
    )
    started = next((r.get("timestamp") for r in rows if r.get("timestamp")), "unknown")
    entrypoint = next((r.get("entrypoint") for r in rows if r.get("entrypoint")), "")
    surface = ENTRYPOINTS.get(entrypoint, entrypoint or "unknown surface")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target, created = choose_target(out_dir, started, args.slug)

    body = render_body(rows)

    redactions = []
    if counts["pdf"]:
        redactions.append(f"the assignment PDF's content ({counts['pdf']}×)")
    if counts["email"]:
        redactions.append(f"e-mail addresses ({counts['email']}×)")
    if counts["secret"]:
        redactions.append(f"secret-like strings ({counts['secret']}×)")
    if len(redactions) > 1:
        redactions[-1] = "and " + redactions[-1]
    joiner = ", " if len(redactions) > 2 else " "
    redaction_note = " Redactions, noted inline: " + joiner.join(redactions) + "." if redactions else ""

    header = (
        f"# Claude Code session: {target.stem}\n\n"
        f"- Tool: Claude Code ({surface}), model `{model}`\n"
        f"- Started: {started}\n"
        "- Export: raw transcript, converted to Markdown by script without edits. "
        "The model's reasoning (\"thinking\") blocks are left out."
        f"{redaction_note}\n"
        "- The export was run during the session's last turn, so the log ends partway through that turn.\n\n"
        "---\n\n"
    )
    text = header + "\n".join(body)
    target.write_text(text, encoding="utf-8")

    summary = {
        "file": str(target),
        "action": "created" if created else "refreshed",
        "transcript": transcript.name,
        "started": started,
        "model": model,
        "messages": len(rows),
        "reasoning_blocks_left_out": counts["thinking"],
        "redacted_documents": counts["pdf"],
        "redacted_emails": counts["email"],
        "redacted_secrets": counts["secret"],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
