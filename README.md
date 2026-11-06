# short notes

A Python reimplementation of the "short notes" that Claude Code builds for background-subagent status labels.

On 2026-11-04 Anthropic said background-subagent status updates now use Haiku 5.5 with "short notes". This script shows what those notes are. Status updates used to fork the subagent's whole conversation into a request on its own model. Now plain code builds a compact notes block from the subagent's recent messages, and a small model turns that block into a 3-5 word label.

## What goes into the notes

- The subagent's last 60 messages. For a forked agent, only the part after the fork, so the parent conversation it inherited is left out.
- The task prompt, cut to 800 characters, then halved until it fits half the budget.
- One line per tool call: `did:` for finished calls, `(failed)` for errors, and a `<started_not_finished>` section for calls still running.
- Short snippets of what the subagent said (`said:`), cut to 300 characters.
- A `<tools-used-before-summary>` line with every tool name used so far.
- A budget of about 1,450 estimated tokens (2,000 minus a 500 reserve and the section markers). The newest steps are kept first.

Tool results, file contents and thinking are never included. Claude Code rebuilds the notes every 30 seconds and skips the call when they haven't changed.

## Usage

```
python3 -I notes_port.py <agent-*.jsonl> [rows]
python3 -I notes_port.py --check <agent-*.jsonl> <request.json>
```

The first form prints the notes for a subagent transcript, optionally as they were after the first `rows` transcript rows. Subagent transcripts live under `~/.claude/projects/<project>/<session>/subagents/`.

The second form takes a captured `agent_summary` request body (for example from mitmproxy) and finds the point in the transcript where the script builds exactly the same notes. It exits 1 if there is no match.

The script runs a small self-test on every start. No dependencies beyond the standard library.

## How it was checked

Against Claude Code 2.1.318 on a claude.ai Pro login. Two real `agent_summary` requests to `claude-haiku-5-5` were captured with mitmproxy, and the script reproduces both notes blocks byte for byte (1,252 and 1,690 characters).

Short mode only ran with a first-party login and no `ANTHROPIC_SMALL_FAST_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL` or custom `ANTHROPIC_BASE_URL`. Otherwise Claude Code falls back to the old fork. To capture requests yourself, use an HTTPS proxy (`HTTPS_PROXY` plus `NODE_EXTRA_CA_CERTS`), not a base-URL proxy. Captured bodies include `metadata.user_id`, so don't publish them.

## Limits

- Whitespace and XML escaping are approximated.
- Activity descriptions cover the built-in tools only. Other tools fall back to the bare tool name, as Claude Code does.
- Matched against one Claude Code version. Later versions may change the format.

Not affiliated with Anthropic.
