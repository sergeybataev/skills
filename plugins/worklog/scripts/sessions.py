#!/usr/bin/env python3
"""Session effort and llm-wiki promotion for a window.

  sessions.py effort  --since S [--until U] [--json]   # tool calls inside the window, by branch
  sessions.py status  --since S [--until U]            # llm-wiki digests in window: total / unpromoted
  sessions.py promote --since S [--until U] --note topics/<topic>/raw/notes/<file>.md
  sessions.py verify  --since S [--until U]            # re-count after promote; live sessions may revert

effort reads Claude Code transcripts (per-message timestamps), so a session resumed from
July counts only what it did inside the window. Without transcripts it falls back to
digests, counting only sessions *started* in the window (their counts are lifetime totals).
"""
import argparse, collections, datetime as dt, json, os, re, sqlite3, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wlconfig  # noqa: E402

CFG = wlconfig.load()
UTC = dt.timezone.utc


def minute_in(m, since, until):
    t = dt.datetime.fromisoformat(m + ":00").replace(tzinfo=UTC)
    return since <= t <= until


def effort(since, until):
    s, u = CFG.bound(since).astimezone(UTC), CFG.bound(until, end=True).astimezone(UTC)
    if CFG.on("transcripts"):
        import session_index  # refreshes the transcript cache (only new/changed files are read)
        con = sqlite3.connect(session_index.DB)
        rows = session_index.load_transcripts(con)
        by_branch = collections.defaultdict(lambda: [0, 0, set()])  # calls, calls from worktree, sessions
        for r in rows:
            for k, n in (r.get("effort") or {}).items():
                h, b, wt = k.split("|")
                if minute_in(h, s, u):
                    e = by_branch[b or "(no branch)"]
                    e[0] += n
                    e[1] += n * int(wt)
                    e[2].add(r["sid"])
        src = "transcripts (per-message timestamps)"
    else:
        by_branch = collections.defaultdict(lambda: [0, 0, set()])
        for d in digests():
            st = d["started"]
            if st and CFG.in_window(st, since, until):
                e = by_branch[d["branch"] or "(no branch)"]
                e[0] += d["tools"]
                e[1] += d["tools"] * d["wt"]
                e[2].add(d["sid"])
        src = "llm-wiki digests (sessions started in window only)"
    total = sum(v[0] for v in by_branch.values()) or 1
    out = [{"branch": b, "tool_calls": n, "share": round(100 * n / total), "sessions": len(ss),
            "worktree_share": round(100 * w / n) if n else 0,
            "note": "" if (n and w * 2 >= n) else "mostly primary checkout — may be a checkout artefact"}
           for b, (n, w, ss) in sorted(by_branch.items(), key=lambda kv: -kv[1][0])]
    return {"source": src, "total_tool_calls": sum(v[0] for v in by_branch.values()), "branches": out}


def digests():
    out = []
    if not CFG.digests or not os.path.isdir(CFG.digests):
        return out
    g = lambda t, k: (re.search(rf'^{k}:\s*"?(.*?)"?\s*$', t, re.M) or [None, ""])[1]
    for root, _, files in os.walk(CFG.digests):
        for f in files:
            if not f.endswith(".md"):
                continue
            p = os.path.join(root, f)
            t = open(p, errors="ignore").read(4000)
            cwd = g(t, "cwd")
            if not CFG.owns_cwd(cwd):
                continue
            pm = g(t, "promoted_to").strip()
            out.append({"path": p, "sid": g(t, "native_session_id"), "branch": g(t, "git_branch"),
                        "started": g(t, "started_at"), "last_seen": g(t, "last_seen_at"),
                        "tools": int(g(t, "tool_event_count") or 0), "wt": int("/.worktrees/" in cwd),
                        "promoted": pm not in ("[]", "", "~", "null")})
    return out


def in_win(ds, since, until):
    return [d for d in ds if CFG.in_window(d["last_seen"], since, until)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["effort", "status", "promote", "verify"])
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default=None)
    ap.add_argument("--note")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    until = a.until or CFG.now().strftime("%Y-%m-%d %H:%M")

    if a.cmd == "effort":
        e = effort(a.since, until)
        if a.json:
            print(json.dumps(e, indent=2))
            return
        print(f"Effort {a.since} → {until}: {e['total_tool_calls']} tool calls · source: {e['source']}\n")
        print("| Branch | Tool calls | Share | Sessions | From worktree | |\n|---|---:|---:|---:|---:|---|")
        for b in e["branches"]:
            print(f"| `{b['branch']}` | {b['tool_calls']} | {b['share']}% | {b['sessions']} | {b['worktree_share']}% | {b['note']} |")
        return

    if not (CFG.on("sessions") and CFG.digests):
        sys.exit("llm-wiki digests are off (wiki.enabled / features.sessions) — nothing to promote")
    ds = in_win(digests(), a.since, until)
    if a.cmd in ("status", "verify"):
        up = [d for d in ds if not d["promoted"]]
        print(json.dumps({"in_window": len(ds), "promoted": len(ds) - len(up), "unpromoted": len(up),
                          "unpromoted_sessions": [d["sid"][:8] for d in up]}))
        return
    if not a.note:
        sys.exit("promote needs --note topics/<topic>/raw/notes/<file>.md")
    n = 0
    for d in ds:
        if d["promoted"]:
            continue
        t = open(d["path"], errors="ignore").read()
        m = re.search(r"^promoted_to:\s*(.*)$", t, re.M)
        if not m:
            continue
        open(d["path"], "w").write(t[:m.start()] + f'promoted_to: ["{a.note}"]' + t[m.end():])
        n += 1
    print(json.dumps({"in_window": len(ds), "promoted_now": n}))


if __name__ == "__main__":
    main()
