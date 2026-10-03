#!/usr/bin/env python3
"""
session-index — map my PRs / tickets / branches to the Claude (and Codex)
sessions that worked on them, with the folder to resume from.

Writes
  Worklog/sessions.sqlite        queryable index (source of truth)
  Worklog/Sessions index.md      readable view, grouped by PR, newest first

Linking evidence, strongest first
  branch   session's git branch == the PR's head branch
  mention  the transcript names the PR (URL or #number) or the ticket key

Sessions started in the primary checkout carry whatever branch happened to be
checked out, so a branch match there is weak; worktree sessions are reliable.
Sessions that mention more than SURVEY_LIMIT distinct PRs (worklog syncs,
reviews of everything) are tagged `survey` and their mentions are not linked.

Usage
  session_index.py                 # rebuild everything
  session_index.py --no-github     # reuse cached PR list in the db
  session_index.py --pr 14533      # print resume commands for one PR
  session_index.py --ticket ABC-123
  session_index.py --branch feature/my-branch
"""

import argparse
import collections
import json
import os
import re
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wlconfig  # noqa: E402

CFG = wlconfig.load()
DIGESTS = CFG.digests
DB = CFG.p["sessions_db"]
MD = CFG.p["sessions_index"]
GH_USER = CFG.github_user
SURVEY_LIMIT = 10
MENTION_TOP = 3     # a session links only its top-N most-mentioned PRs
MENTION_MIN = 3

TICKET_RE = re.compile("(" + CFG.ticket_re.pattern + ")", re.I)
PR_URL_RE = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
PR_NUM_RE = re.compile(r"#(\d{3,6})\b")   # bare #NN collides with too much; URLs still count


def fm(text, key):
    m = re.search(rf'^{key}:\s*"?(.*?)"?\s*$', text, re.M)
    return m.group(1) if m else ""


def rel_folder(cwd):
    """Path relative to the code root that contains it, prefixed with the root's name."""
    for r in CFG.code_roots:
        r = r.rstrip("/")
        if cwd == r or cwd.startswith(r + "/"):
            return os.path.basename(r) + cwd[len(r):]
    return cwd


def resume_cmd(harness, sid, cwd):
    tool = "codex resume" if harness == "codex" else "claude --resume"
    return f"cd {cwd.replace(os.path.expanduser('~'), '~')} && {tool} {sid}"


def load_sessions():
    out = []
    for root, _, files in os.walk(DIGESTS):
        for f in files:
            if not f.endswith(".md"):
                continue
            with open(os.path.join(root, f), errors="ignore") as fh:
                t = fh.read(4000)
            cwd = fm(t, "cwd")
            if not CFG.owns_cwd(cwd):
                continue
            tp = fm(t, "transcript_path")
            out.append({
                "sid": fm(t, "native_session_id"),
                "harness": fm(t, "harness") or "claude",
                "cwd": cwd,
                "folder": rel_folder(cwd),
                "worktree": int("/.worktrees/" in cwd or "/worktrees/" in cwd),
                "branch": fm(t, "git_branch"),
                "started": fm(t, "started_at"),
                "last_seen": fm(t, "last_seen_at"),
                "tool_events": int(fm(t, "tool_event_count") or 0),
                "transcript": tp,
                "resumable": int(bool(tp) and os.path.exists(tp)),
            })
    return [s for s in out if s["sid"]]


def scan_transcript(path, my_prs):
    """(pr mention counter keyed (repo,num), ticket counter)."""
    prs, tickets = collections.Counter(), collections.Counter()
    if not path or not os.path.exists(path):
        return prs, tickets
    # a bare #N counts only when exactly one of my PRs, across all repos, has that number
    owners = collections.defaultdict(list)
    for (r, n) in my_prs:
        owners[n].append(r)
    unique = {n: rs[0] for n, rs in owners.items() if len(rs) == 1}
    with open(path, errors="ignore") as fh:
        for line in fh:
            for repo, n in PR_URL_RE.findall(line):
                if (repo, int(n)) in my_prs:
                    prs[(repo, int(n))] += 1
            for n in PR_NUM_RE.findall(line):
                if int(n) in unique:
                    prs[(unique[int(n)], int(n))] += 1
            for k in TICKET_RE.findall(line):
                tickets[k.upper()] += 1
    return prs, tickets


def fetch_prs():
    rows = []
    for repo in CFG.repos:
        r = subprocess.run(
            ["gh", "pr", "list", "-R", repo, "--author", GH_USER,
             "--state", "all", "--limit", "400",
             "--json", "number,title,headRefName,state,createdAt,mergedAt,isDraft,url"],
            capture_output=True, text=True)
        if r.returncode != 0:
            print(f"warn: {repo}: {r.stderr.strip()[:120]}", file=sys.stderr)
            continue
        for p in json.loads(r.stdout or "[]"):
            state = "MERGED" if p.get("mergedAt") else ("DRAFT" if p.get("isDraft") and p["state"] == "OPEN" else p["state"])
            rows.append((repo, p["number"], p["title"], p["headRefName"], state,
                         p["createdAt"][:10], (p.get("mergedAt") or "")[:10], p["url"]))
    return rows


def build(args):
    con = sqlite3.connect(DB)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS prs(repo, number INT, title, branch, state, created, merged, url,
        PRIMARY KEY(repo, number));
    DROP TABLE IF EXISTS sessions; DROP TABLE IF EXISTS links;
    CREATE TABLE sessions(sid PRIMARY KEY, harness, folder, cwd, worktree INT, branch,
        started, last_seen, tool_events INT, resumable INT, survey INT, resume);
    CREATE TABLE links(sid, kind, key, evidence, weight INT);
    """)
    if not args.no_github:
        user = subprocess.run(["gh", "api", "user", "-q", ".login"], capture_output=True, text=True).stdout.strip()
        if user != GH_USER:
            sys.exit(f"gh is '{user}', not {GH_USER} — run: gh auth switch --user {GH_USER}")
        rows = fetch_prs()
        if rows:
            con.execute("DELETE FROM prs")
            con.executemany("INSERT INTO prs VALUES (?,?,?,?,?,?,?,?)", rows)
    prs = {(r, n): b for r, n, b in con.execute("SELECT repo, number, branch FROM prs")}
    by_branch = collections.defaultdict(list)
    for k, b in prs.items():
        by_branch[b].append(k)

    sessions = load_sessions()
    for i, s in enumerate(sessions, 1):
        pm, tk = scan_transcript(s["transcript"], prs)
        survey = int(len(pm) > SURVEY_LIMIT)
        con.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            s["sid"], s["harness"], s["folder"], s["cwd"], s["worktree"], s["branch"],
            s["started"], s["last_seen"], s["tool_events"], s["resumable"], survey,
            resume_cmd(s["harness"], s["sid"], s["cwd"])))
        for k in by_branch.get(s["branch"], []):
            ev = "branch" if s["worktree"] else "branch-checkout"
            con.execute("INSERT INTO links VALUES (?,?,?,?,?)", (s["sid"], "pr", f"{k[0]}#{k[1]}", ev, 1000))
        if s["branch"]:
            con.execute("INSERT INTO links VALUES (?,?,?,?,?)", (s["sid"], "branch", s["branch"], "branch", 1000))
            for t in TICKET_RE.findall(s["branch"].upper()):
                con.execute("INSERT INTO links VALUES (?,?,?,?,?)", (s["sid"], "ticket", t, "branch", 1000))
        if not survey:
            for (repo, n), c in pm.most_common(MENTION_TOP):
                if c < MENTION_MIN:
                    break
                con.execute("INSERT INTO links VALUES (?,?,?,?,?)", (s["sid"], "pr", f"{repo}#{n}", "mention", c))
            for t, c in tk.most_common(10):
                con.execute("INSERT INTO links VALUES (?,?,?,?,?)", (s["sid"], "ticket", t, "mention", c))
        if i % 50 == 0:
            print(f"  scanned {i}/{len(sessions)}", file=sys.stderr)
    con.commit()
    write_md(con)
    n_l = con.execute("SELECT count(DISTINCT key) FROM links WHERE kind='pr'").fetchone()[0]
    print(f"{len(sessions)} sessions, {len(prs)} PRs, {n_l} PRs linked to ≥1 session → {DB}")


def write_md(con):
    q = """
    SELECT p.repo, p.number, p.title, p.state, p.created, p.merged, p.url, p.branch,
           s.sid, s.folder, s.worktree, s.last_seen, s.tool_events, s.resumable, s.resume,
           group_concat(DISTINCT l.evidence), max(l.weight)
    FROM prs p JOIN links l ON l.kind='pr' AND l.key = p.repo || '#' || p.number
    JOIN sessions s ON s.sid = l.sid
    WHERE 1
    GROUP BY p.repo, p.number, s.sid
    ORDER BY p.created DESC, s.last_seen DESC"""
    groups = collections.OrderedDict()
    for r in con.execute(q):
        groups.setdefault(r[:8], []).append(r[8:])
    L = ["# Sessions index", "",
         "> Generated by `bin/session_index.py` — don't edit by hand, rerun it. "
         "Source of truth: `sessions.sqlite`.", "",
         "Each PR lists the sessions that worked on it, strongest evidence first. "
         "**Evidence:** `branch` = worktree on the PR's branch (reliable) · "
         "`branch-checkout` = main checkout happened to be on it (weak) · "
         "`mention` = transcript names the PR ≥3×. ✗ = transcript gone, can't resume.", "",
         "Folders are relative to `~/go/src/github.com/`.", ""]
    rank = {"branch": 0, "mention": 1, "branch-checkout": 2}
    for (repo, num, title, state, created, merged, url, branch), ss in groups.items():
        ss.sort(key=lambda x: (min(rank.get(e, 3) for e in x[7].split(",")), -(x[8] or 0), -(x[4] or 0)))
        L.append(f"### [{repo} #{num}]({url}) — {title}")
        L.append(f"`{state}` · opened {created}" + (f" · merged {merged}" if merged else "") + f" · branch `{branch}`")
        L.append("")
        L.append("| Session | Folder | Last seen | Events | Evidence | Resume |")
        L.append("|---|---|---|---:|---|---|")
        for sid, folder, wt, last, ev, ok, cmd, evid, _ in ss[:6]:
            L.append(f"| `{sid[:8]}` | `{folder}`{' 🌿' if wt else ''} | {last[:10]} | {ev} | {evid} | "
                     + (f"`{cmd}`" if ok else "✗") + " |")
        if len(ss) > 6:
            L.append(f"| … | {len(ss)-6} more — `session_index.py --pr {num}` | | | | |")
        L.append("")
        L.append("")
    orphan = con.execute("""
        SELECT s.branch, s.folder, max(s.last_seen), sum(s.tool_events), count(*),
               (SELECT resume FROM sessions x WHERE x.branch=s.branch AND x.resumable=1
                ORDER BY x.last_seen DESC LIMIT 1)
        FROM sessions s
        WHERE s.branch != '' AND s.branch NOT IN (SELECT branch FROM prs)
          AND s.last_seen >= date('now','-45 days') AND s.worktree = 1
        GROUP BY s.branch ORDER BY max(s.last_seen) DESC""").fetchall()
    if orphan:
        L[8:8] = ["## Branches with no PR (worktree sessions, last 45 days)", "",
                  "| Branch | Folder | Last seen | Events | Sessions | Resume latest |",
                  "|---|---|---|---:|---:|---|"] + [
            f"| `{b}` | `{f}` | {ls[:10]} | {ev} | {n} | " + (f"`{cmd}`" if cmd else "✗") + " |"
            for b, f, ls, ev, n, cmd in orphan] + ["", "## By PR", ""]
    with open(MD, "w") as f:
        f.write("\n".join(L) + "\n")


def lookup(args):
    con = sqlite3.connect(DB)
    if args.pr:
        kind, key = "pr", f"{args.repo}#{args.pr}"
    elif args.branch:
        kind, key = "branch", args.branch
    else:
        kind, key = "ticket", args.ticket.upper()
    rows = con.execute("""
        SELECT s.last_seen, s.tool_events, group_concat(DISTINCT l.evidence), s.resumable, s.resume,
               min(CASE l.evidence WHEN 'branch' THEN 0 WHEN 'mention' THEN 1 ELSE 2 END) AS r,
               max(CASE l.evidence WHEN 'mention' THEN l.weight ELSE 0 END) AS w
        FROM links l JOIN sessions s ON s.sid=l.sid WHERE l.kind=? AND l.key=?
        GROUP BY s.sid ORDER BY r, w DESC, s.tool_events DESC""", (kind, key)).fetchall()
    if not rows:
        sys.exit(f"no sessions linked to {key}")
    for last, ev, evid, ok, cmd, _, _ in rows:
        print(f"{last[:16]}  {ev:>5} ev  {evid:<22} {cmd if ok else '(transcript gone) ' + cmd}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-github", action="store_true")
    ap.add_argument("--pr", type=int)
    ap.add_argument("--repo", default=(CFG.repos[0] if CFG.repos else ""),
                    help="owner/name; defaults to the first repo in config")
    ap.add_argument("--ticket")
    ap.add_argument("--branch")
    a = ap.parse_args()
    if a.pr or a.ticket or a.branch:
        lookup(a)
    else:
        build(a)


if __name__ == "__main__":
    main()
