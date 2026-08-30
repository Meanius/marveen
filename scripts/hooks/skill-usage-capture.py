#!/usr/bin/env python3
"""PostToolUse hook: log skill usage to the persistent skill_usage table.

Two event types are captured:
  tool_call  -- the Skill tool was invoked (tool_name == 'Skill')
  skill_read -- a SKILL.md file under ~/.claude/skills/<name>/SKILL.md was Read

Unlike tool_call_log (pruned every 24 h), skill_usage is never pruned so the
dream-engine can make data-driven suggestions after two or more weeks of data.

Registration (user-level ~/.claude/settings.json, post-install step):
  The hook command uses a guard so it silently no-ops if the file is missing
  (e.g. on develop before merging this feature):

    "command": "test -f /path/to/scripts/hooks/skill-usage-capture.py && python3 ... || true"

  This means the hook can be registered before merge without causing errors
  on other branches where the file does not yet exist.
"""
import sys
import os
import re
import json
import urllib.request
import urllib.error

# Agent identity comes from the ledger library -- ONE resolver for every hook.
# This file used to carry its own _agent_id_from_cwd() copy, which drifted:
# it missed the install-subdirectory case (only `cwd == install` mapped to the
# main agent) and kept the basename fallback, so skill_usage rows got an agent
# id INVENTED from the directory name. A drifted private copy is exactly how
# that class of bug survives -- delegate instead of duplicating.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger_lib  # noqa: E402


def _install_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _web_port() -> str:
    port = os.environ.get("WEB_PORT")
    if not port:
        try:
            with open(os.path.join(_install_dir(), ".env")) as f:
                for line in f:
                    if line.startswith("WEB_PORT="):
                        port = line.split("=", 1)[1].strip().strip('"')
                        break
        except Exception:
            pass
    return port or "3420"


def _dashboard_token() -> str:
    try:
        with open(os.path.join(_install_dir(), "store", ".dashboard-token")) as f:
            return f.read().strip()
    except OSError:
        return ""


# Kept as a module-level alias for the tests (they pin that this file has no
# private copy of the resolver); the hook itself resolves from the PAYLOAD
# (transcript-anchored, LEDGERCWD828), not from this cwd-only fallback.
_agent_id_from_cwd = ledger_lib.agent_id_from_cwd


# ~/.claude/skills/<name>/SKILL.md  (expand ~ for the running user)
_SKILL_MD_RE = re.compile(
    r"^" + re.escape(os.path.expanduser("~")) + r"/\.claude/skills/([^/]+)/SKILL\.md$"
)

# The same path anywhere inside a shell command line. In bypass-permissions mode
# skills are read with `cat`/`head`/`sed`, not with the Read tool, so without this
# branch the counter stays silent for the reads that actually happen and the
# dream-engine would conclude "nobody uses this skill".
# The home prefix may be literal, `~` or `$HOME`; the name must be a concrete
# segment, so glob characters are excluded (`wc -l ~/.claude/skills/*/SKILL.md`
# must not be recorded as a skill called `*`).
_SKILL_MD_IN_CMD_RE = re.compile(
    r"(?:" + re.escape(os.path.expanduser("~")) + r"|~|\$HOME|\$\{HOME\})"
    r"/\.claude/skills/([^/\s*?\[\]\"\'`]+)/SKILL\.md"
)


# A heredoc body is data the command carries, not a file it touches. A commit
# message quoting `~/.claude/skills/handoff/SKILL.md`, or a python patch script
# whose source mentions the path, logged a read on 2026-08-30 -- twice for the
# same commit, because the message was written twice. Only the part of the
# command line BEFORE the first heredoc marker is treated as file access.
_HEREDOC_RE = re.compile(r"<<-?\s*[\'\"]?\w+")

# `> path` / `>> path` immediately before the match means the file is written.
_REDIRECT_BEFORE_RE = re.compile(r">>?\s*[\'\"]?$")


def _command_head(command: str) -> str:
    m = _HEREDOC_RE.search(command)
    return command[:m.start()] if m else command


def _classify(tool_name: str, tool_input: dict) -> tuple[str, str] | None:
    """Return (skill_name, trigger_type) or None if this event is irrelevant."""
    if tool_name == "Skill":
        skill = (tool_input.get("skill") or "").strip()
        if skill:
            return skill, "tool_call"
    elif tool_name == "Read":
        path = (tool_input.get("file_path") or "").strip()
        m = _SKILL_MD_RE.match(path)
        if m:
            return m.group(1), "skill_read"
    elif tool_name == "Bash":
        command = _command_head(tool_input.get("command") or "")
        for m in _SKILL_MD_IN_CMD_RE.finditer(command):
            name = m.group(1)
            # The path must resolve to a skill that actually exists. A command
            # can mention the pattern without reading anything real: the very
            # first probe of this branch logged `<name>` (from a doc comment)
            # and `agent-x` (from a test fixture), neither of which is a skill.
            # An existence check is the cheapest filter that removes both.
            if not os.path.isfile(os.path.expanduser(f"~/.claude/skills/{name}/SKILL.md")):
                continue
            # A redirection target is being WRITTEN, not read: `cat > .../SKILL.md`
            # creating a new skill must not count as using it.
            if _REDIRECT_BEFORE_RE.search(command[:m.start()]):
                continue
            # Only the first existing skill is recorded: one hook invocation
            # posts one row, and a command touching several SKILL.md files at
            # once is rare enough not to justify changing that contract.
            return name, "skill_read"
    return None


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    session_id = payload.get("session_id") or None
    cwd = payload.get("cwd") or ""

    result = _classify(tool_name, tool_input)
    if result is None:
        sys.exit(0)

    skill_name, trigger_type = result
    agent_id = ledger_lib.agent_id_from_payload(payload)

    token = _dashboard_token()
    if not token:
        sys.exit(0)

    body = json.dumps({
        "agent_id": agent_id,
        "skill_name": skill_name,
        "trigger_type": trigger_type,
        "session_id": session_id,
    }).encode()

    try:
        urllib.request.urlopen(
            urllib.request.Request(
                f"http://localhost:{_web_port()}/api/skill-usage",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                method="POST",
            ),
            timeout=3,
        )
    except Exception:
        pass  # never block the agent

    sys.exit(0)


if __name__ == "__main__":
    main()
