"""Python reimplementation of the "short notes" that Claude Code 2.1.318 builds for background-subagent
status labels, based on observed behaviour.

Reads a subagent transcript .jsonl and prints the notes block the short path would send to the small model.
Matched byte-for-byte against two real agent_summary requests captured with mitmproxy on 2026-11-06.

Usage:
  python3 -I notes_port.py <agent-*.jsonl> [rows]       print the notes after the first <rows> transcript rows
  python3 -I notes_port.py --check <agent-*.jsonl> <request.json>
                                                       find the row count whose notes equal a captured request

ponytail: text normalisation is approximated (whitespace collapse + XML escape); tool descriptions cover the
built-in tools only, MCP and other tools fall back to the bare tool name like the real code does.
"""
import json, os, sys, re
from xml.sax.saxutils import escape

HOME = os.path.expanduser("~")
tilde = lambda p: "~" + p[len(HOME):] if isinstance(p, str) and p.startswith(HOME) else p

def describe(name, i):
    """Each tool's own getActivityDescription(input), as shown in the agent panel (2.1.318)."""
    g = lambda k: i.get(k) if isinstance(i.get(k), str) and i.get(k) else None
    table = {
        "Read": lambda: f"Reading {tilde(g('file_path'))}" if g("file_path") else "Reading file",
        "Write": lambda: f"Writing {tilde(g('file_path'))}" if g("file_path") else "Writing file",
        "Edit": lambda: f"Editing {tilde(g('file_path'))}" if g("file_path") else "Editing file",
        "NotebookEdit": lambda: f"Editing notebook {tilde(g('notebook_path'))}" if g("notebook_path") else "Editing notebook",
        "Glob": lambda: f"Finding {g('pattern')}" if g("pattern") else "Finding files",
        "Grep": lambda: f"Searching for {g('pattern')}" if g("pattern") else "Searching",
        "WebFetch": lambda: f"Fetching {g('url')}" if g("url") else "Fetching web page",
        "WebSearch": lambda: f"Searching for {g('query')}" if g("query") else "Searching the web",
        "Monitor": lambda: f"Monitoring: {g('description')}" if g("description") else "Monitoring",
    }
    return table[name]() if name in table else ""

TASK_MAX, SAID, TOOL, CMD, CMD_CAP, LAST_N = 800, 300, 160, 120, 10_000, 60
BUDGET = 2000 - 500  # notes budget minus reserve, in estimated tokens
PAREN_O, PAREN_C = "(（", ")）"

def tok(s):  # token estimate: ascii/3 + non-ascii
    n = sum(1 for c in s if ord(c) > 127)
    return (len(s) - n) / 3 + n

def norm(s):
    return re.sub(r"\s+", " ", s).strip()

def clip(s, n):
    s = s.lstrip(); y = s[:4 * n]; r = norm(y); m = r[:n]
    if not m: return ""
    cut = len(m) < len(r) or len(y) < len(s)
    return escape(m + "…" if cut else m)

def flatten_cmd(cmd):
    x = " ↵ ".join(filter(None, (norm(l) for l in re.split(r"[\r\n]", cmd[:CMD_CAP]))))
    return x + " …" if len(cmd) > CMD_CAP else x

FORK_MARK = "<fork-boilerplate>"

def is_fork_mark(m):
    """User message that opens a forked agent's own part (text starting with <fork-boilerplate>)."""
    c = m.get("message", {}).get("content")
    return m["type"] == "user" and isinstance(c, list) and any(
        b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"].startswith(FORK_MARK) for b in c)

def counts(m):
    """Assistant messages the builder reads: not virtual, not a synthetic API-error message."""
    return not m.get("isVirtual") and not (m.get("isApiErrorMessage") and m.get("message", {}).get("model") == "<synthetic>")

def build(task, msgs, describe=describe):
    msgs = msgs[-LAST_N:]
    cut = max((i for i, m in enumerate(msgs) if is_fork_mark(m)), default=-1)
    msgs = msgs[cut + 1:]  # forked agents: drop the inherited parent conversation
    entries, by_id, last_id, user_spoke = [], {}, None, False
    for m in msgs:
        msg = m.get("message", {}); c = msg.get("content")
        if m["type"] == "user":
            blocks = c if isinstance(c, list) else []
            user_spoke |= (not m.get("isMeta") and not any(b.get("type") == "tool_result" for b in blocks)
                           and (isinstance(c, str) or any(b.get("type") == "text" for b in blocks)))
            for b in blocks:
                if b.get("type") == "tool_result" and b.get("tool_use_id") in by_id:
                    by_id[b["tool_use_id"]]["call"] = "failed" if b.get("is_error") else "done"
        elif m["type"] == "assistant" and counts(m) and isinstance(c, list):
            rid = msg.get("id"); last_id = rid; user_spoke = False
            for b in c:
                if b.get("type") == "text":
                    t = clip(b.get("text", ""), SAID)
                    if t: entries.append({"text": f"said: {t}", "rid": rid})
                elif b.get("type") == "tool_use":
                    inp = b.get("input") or {}; name = b["name"]
                    desc = inp.get("description", "") if isinstance(inp.get("description"), str) else ""
                    z = flatten_cmd(inp["command"]) if name in ("Bash", "PowerShell") and isinstance(inp.get("command"), str) else ""
                    y = next((v for v in (desc, z) if clip(v, TOOL)), None) or describe(name, inp)
                    le = clip(f"{name} - {y}" if y else name, TOOL)
                    j = clip(z, CMD) if y == desc else ""
                    pe = clip(desc, TOOL)
                    for o in PAREN_O: pe = pe.replace(o, "[")
                    for cl in PAREN_C: pe = pe.replace(cl, "]")
                    ve = f"{clip(name, TOOL)} - {j} (described as: {pe})" if j else le
                    e = {"text": ve, "call": "open", "rid": rid}; entries.append(e); by_id[b["id"]] = e
    items = []
    for e in entries:
        if "call" not in e: items.append((False, e["text"]))
        elif e["call"] != "open": items.append((False, f"did: {e['text']}" + (" (failed)" if e["call"] == "failed" else "")))
        elif e["rid"] == last_id and not user_spoke: items.append((True, e["text"]))
    if not items: return ""
    x, r = "", TASK_MAX
    while r >= 50:
        t = clip(task, r); k = f"<task>\n{t}\n</task>" if t else ""
        if tok(k) <= BUDGET / 2: x = k; break
        r //= 2
    h, start = BUDGET - tok(x), len(items) - 1
    for i in range(len(items) - 1, -1, -1):
        h -= tok(items[i][1])
        if h < 0: break
        start = i
    keep = items[start:]
    sec = lambda tag, xs: f"<{tag}>\n" + "\n".join(xs) + f"\n</{tag}>" if xs else ""
    return "\n".join(filter(None, [x, sec("recent_steps", [t for o, t in keep if not o]),
                                   sec("started_not_finished", [t for o, t in keep if o])]))

def tools_used(msgs):
    """Label line sent after the notes: every tool name the agent has called so far, sorted."""
    names = sorted({b["name"] for m in msgs if m["type"] == "assistant" and isinstance(m["message"].get("content"), list)
                    for b in m["message"]["content"] if b.get("type") == "tool_use"})
    return f"<tools-used-before-summary>{', '.join(names)}</tools-used-before-summary>" if names else ""

def demo():
    msgs = [{"type": "user", "message": {"content": "do it"}},
            {"type": "assistant", "message": {"id": "a1", "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "x.py"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}]}},
            {"type": "assistant", "message": {"id": "a2", "content": [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "sleep 15", "description": "Wait"}}]}}]
    out = build("count things", msgs)
    assert "did: Read - Reading x.py" in out and "<started_not_finished>\nBash - sleep 15 (described as: Wait)" in out, out
    fork = [{"type": "assistant", "message": {"id": "p", "content": [{"type": "text", "text": "parent talk"}]}},
            {"type": "user", "message": {"content": [{"type": "text", "text": FORK_MARK + "go"}]}},
            {"type": "assistant", "isVirtual": True, "message": {"id": "v", "content": [{"type": "text", "text": "virtual"}]}}] + msgs
    out2 = build("count things", fork)
    assert out2 == out and "parent talk" not in out2 and "virtual" not in out2, out2

def load(path):
    rows = [json.loads(l) for l in open(path) if l.strip()]
    rows = [r for r in rows if r.get("type") in ("user", "assistant")]
    if not rows: sys.exit(f"no user/assistant rows in {path}")
    first = rows[0]["message"]["content"]
    task = first if isinstance(first, str) else " ".join(b.get("text", "") for b in first if b.get("type") == "text")
    return task, rows

def notes_at(task, rows, n):
    return "\n".join(filter(None, [build(task, rows[:n]), tools_used(rows[:n])]))

def captured_notes(req_path):
    """Notes part of a captured agent_summary request body: the user message up to the instructions."""
    c = json.load(open(req_path))["messages"][0]["content"]
    text = c if isinstance(c, str) else "".join(b.get("text", "") for b in c if b.get("type") == "text")
    return text.split("\n\nDescribe the agent's", 1)[0]

if __name__ == "__main__":
    demo()
    a = sys.argv[1:]
    if a[:1] == ["--check"]:
        if len(a) != 3: sys.exit(__doc__)
        task, rows = load(a[1]); want = captured_notes(a[2])
        hits = [n for n in range(1, len(rows) + 1) if notes_at(task, rows, n) == want]
        print(f"match at {hits[0]} transcript rows ({len(want)} chars)" if hits else
              f"no match: captured notes are {len(want)} chars, no prefix of the transcript builds them")
        sys.exit(0 if hits else 1)
    if a:
        task, rows = load(a[0])
        n = int(a[1]) if len(a) > 1 else len(rows)
        notes = notes_at(task, rows, n)
        print(notes); print(f"\n[{len(notes)} chars, ~{tok(notes):.0f} est. tokens, from {n} transcript rows]", file=sys.stderr)
