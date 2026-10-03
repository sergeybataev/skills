#!/usr/bin/env python3
"""
session_index — map my PRs / tickets / branches to the Claude (and Codex) sessions
that worked on them, with the folder to resume from.

Sources (merged by session id)
  transcripts  Claude Code's own transcripts, ~/.claude/projects/*/<id>.jsonl
               ([sessions] transcripts_dir). Always available; parsed once and cached
               by (path, mtime, size), so rebuilds only read new or changed files.
  digests      llm-wiki session digests, when wiki is enabled. Adds what transcripts
               can't: Codex sessions and sessions whose transcript was deleted.

Writes  <paths.sessions_db> (sqlite, source of truth) and <paths.sessions_index> (markdown).

Linking evidence, strongest first
  branch           ≥5% of a session's messages were on the PR's head branch, mostly from a
                   worktree folder (sessions that cd into a worktree count from that point)
  mention          the transcript names the PR (URL or #number) ≥3× and it's among the
                   session's 3 most-mentioned PRs
  branch-checkout  same as branch, but in a primary checkout — whatever was checked out (weak)
Sessions mentioning more than SURVEY_LIMIT distinct PRs (syncs, reviews of everything)
are tagged `survey` and their mentions aren't linked.

Usage
  session_index.py                    # rebuild
  session_index.py --no-github        # reuse cached PR list
  session_index.py --pr 1234 [--repo owner/name]
  session_index.py --ticket ABC-123
  session_index.py --branch feature/my-branch
"""

import argparse
import collections
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wlconfig  # noqa: E402

CFG = wlconfig.load()
DB = CFG.p["sessions_db"]
MD = CFG.p["sessions_index"]
GH_USER = CFG.github_user
PARSER_VERSION = "5"
SURVEY_LIMIT = 10
MENTION_TOP = 3
MENTION_MIN = 3
BRANCH_SHARE = 0.05

TICKET_RE = re.compile("(" + CFG.ticket_re.pattern + ")", re.I)
PR_URL_RE = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
PR_NUM_RE = re.compile(r"#(\d{3,6})\b")   # bare #NN collides with too much; URLs still count
_S = r'"((?:[^"\\]|\\.)*)"'
SID_RE = re.compile(r'"sessionId":"([0-9a-f-]{36})"')
CWD_RE = re.compile(r'"cwd":' + _S)
BR_RE = re.compile(r'"gitBranch":' + _S)
TS_RE = re.compile(r'"timestamp":"([0-9T:.+\-Z]+)"')
TITLE_RE = re.compile(r'"aiTitle":' + _S)
TOOL_USE = '"type":"tool_use"'


def unq(s):
    try:
        return json.loads(f'"{s}"')
    except Exception:
        return s


def rel_folder(cwd):
    """Path relative to the code root that contains it, prefixed with the root's name."""
    for r in CFG.code_roots:
        r = r.rstrip("/")
        if cwd == r or cwd.startswith(r + "/"):
            return os.path.basename(r) + cwd[len(r):]
    return cwd


def is_worktree(cwd):
    return int("/.worktrees/" in cwd or "/worktrees/" in cwd)


def resume_cmd(harness, sid, cwd):
    tool = "codex resume" if harness == "codex" else "claude --resume"
    return f"cd {cwd.replace(os.path.expanduser('~'), '~')} && {tool} {sid}"


# ---------------------------------------------------------------- transcripts

def parse_transcript(path):
    """One streaming pass with regexes — tool outputs make full JSON decoding slow."""
    sid, first_cwd, cur, title, tools = None, "", "", "", 0
    cwds, ts = collections.Counter(), []
    branches = collections.defaultdict(lambda: [0, 0])   # branch -> [messages, messages from a worktree]
    effort = collections.Counter()                         # "YYYY-MM-DDTHH:MM|branch|wt" (UTC minute) -> tool calls
    cur_branch, cur_ts = "", ""
    urls, nums, tickets = collections.Counter(), collections.Counter(), collections.Counter()
    with open(path, errors="ignore") as fh:
        for line in fh:
            if sid is None:
                m = SID_RE.search(line)
                if m:
                    sid = m.group(1)
            m = CWD_RE.search(line)
            if m:
                cur = unq(m.group(1))
                first_cwd = first_cwd or cur
                cwds[cur] += 1
            m = BR_RE.search(line)
            if m and m.group(1) not in ("", "HEAD"):
                cur_branch = unq(m.group(1))
                b = branches[cur_branch]
                b[0] += 1
                b[1] += is_worktree(cur)
            m = TS_RE.search(line)
            if m:
                ts.append(m.group(1))
                cur_ts = m.group(1)
            m = TITLE_RE.search(line)
            if m:
                title = unq(m.group(1))
            if '"type":"assistant"' in line:
                k = line.count(TOOL_USE)
                tools += k
                if k and cur_ts:
                    effort[f"{cur_ts[:16]}|{cur_branch}|{is_worktree(cur)}"] += k
            for repo, n in PR_URL_RE.findall(line):
                urls[f"{repo}#{n}"] += 1
            for n in PR_NUM_RE.findall(line):
                nums[n] += 1
            for k in TICKET_RE.findall(line):
                tickets[k.upper()] += 1
    return {
        "sid": sid or os.path.basename(path)[:-6],
        "cwd": first_cwd,                                         # launch folder — where --resume works
        "main_cwd": cwds.most_common(1)[0][0] if cwds else first_cwd,
        "branches": dict(branches), "started": min(ts) if ts else "", "last_seen": max(ts) if ts else "",
        "tool_events": tools, "title": title, "effort": dict(effort),
        "mentions": {"urls": dict(urls), "nums": dict(nums), "tickets": dict(tickets)},
    }


def load_transcripts(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS meta(k PRIMARY KEY, v);
    CREATE TABLE IF NOT EXISTS tcache(path PRIMARY KEY, mtime REAL, size INT, data TEXT);
    """)
    v = con.execute("SELECT v FROM meta WHERE k='parser'").fetchone()
    if not v or v[0] != PARSER_VERSION:
        con.execute("DELETE FROM tcache")
        con.execute("INSERT OR REPLACE INTO meta VALUES ('parser', ?)", (PARSER_VERSION,))
    cached = {p: (m, s, d) for p, m, s, d in con.execute("SELECT path, mtime, size, data FROM tcache")}
    files = glob.glob(os.path.join(CFG.transcripts_dir, "*", "*.jsonl"))  # top level only — skips subagents
    out, parsed = [], 0
    for i, f in enumerate(files, 1):
        st = os.stat(f)
        c = cached.get(f)
        if c and c[0] == st.st_mtime and c[1] == st.st_size:
            d = json.loads(c[2])
        else:
            d = parse_transcript(f)
            con.execute("INSERT OR REPLACE INTO tcache VALUES (?,?,?,?)", (f, st.st_mtime, st.st_size, json.dumps(d)))
            parsed += 1
            if parsed % 100 == 0:
                con.commit()
                print(f"  parsed {parsed} transcripts ({i}/{len(files)})", file=sys.stderr)
        if not d["cwd"] or not CFG.owns_cwd(d["cwd"]):
            continue
        d.update(harness="claude", transcript=f, resumable=1)
        out.append(d)
    gone = set(cached) - set(files)
    if gone:
        con.executemany("DELETE FROM tcache WHERE path=?", [(p,) for p in gone])
    con.commit()
    print(f"  transcripts: {len(files)} files, {parsed} parsed, {len(files) - parsed} from cache, "
          f"{len(out)} in your code roots", file=sys.stderr)
    return out


# ---------------------------------------------------------------- llm-wiki digests

def fm(text, key):
    m = re.search(rf'^{key}:\s*"?(.*?)"?\s*$', text, re.M)
    return m.group(1) if m else ""


def load_digests():
    out = []
    if not CFG.digests or not os.path.isdir(CFG.digests):
        return out
    for root, _, files in os.walk(CFG.digests):
        for f in files:
            if not f.endswith(".md"):
                continue
            with open(os.path.join(root, f), errors="ignore") as fh:
                t = fh.read(4000)
            cwd, sid = fm(t, "cwd"), fm(t, "native_session_id")
            if not sid or not CFG.owns_cwd(cwd):
                continue
            tp = fm(t, "transcript_path")
            br = fm(t, "git_branch")
            out.append({
                "sid": sid, "harness": fm(t, "harness") or "claude", "cwd": cwd,
                "main_cwd": cwd,
                "branches": {br: [1, is_worktree(cwd)]} if br else {}, "started": fm(t, "started_at"),
                "last_seen": fm(t, "last_seen_at"), "tool_events": int(fm(t, "tool_event_count") or 0),
                "title": "", "transcript": tp, "resumable": int(bool(tp) and os.path.exists(tp)),
                "mentions": None,
            })
    return out


# ---------------------------------------------------------------- PRs

def fetch_prs():
    rows = []
    for repo in CFG.repos:
        r = subprocess.run(
            ["gh", "pr", "list", "-R", repo, "--author", GH_USER, "--state", "all", "--limit", "400",
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


def resolve_mentions(m, my_prs, unique_num):
    """Raw mention counts → Counter over my PRs ('owner/repo#n')."""
    c = collections.Counter()
    if not m:
        return c
    for k, n in m["urls"].items():
        if k in my_prs:
            c[k] += n
    for num, n in m["nums"].items():
        k = unique_num.get(int(num))
        if k:
            c[k] += n
    return c


def mentions_from_file(path, my_prs, unique_num):
    """For digest-only sessions whose transcript still exists outside transcripts_dir."""
    if not path or not os.path.exists(path):
        return None
    return parse_transcript(path)["mentions"]


# ---------------------------------------------------------------- build

def build(args):
    if not CFG.on("session_index"):
        sys.exit("session index is off — set features.session_index = true")
    use_t, use_d = CFG.on("transcripts"), CFG.on("sessions") and bool(CFG.digests)
    if not (use_t or use_d):
        sys.exit("no session source — enable features.transcripts, or wiki + features.sessions")
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    os.makedirs(os.path.dirname(MD), exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS prs(repo, number INT, title, branch, state, created, merged, url,
        PRIMARY KEY(repo, number));
    DROP TABLE IF EXISTS sessions; DROP TABLE IF EXISTS links;
    CREATE TABLE sessions(sid PRIMARY KEY, harness, source, folder, cwd, worktree INT, branch, branches,
        started, last_seen, tool_events INT, title, resumable INT, survey INT, resume);
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
    pr_branch = {f"{r}#{n}": b for r, n, b in con.execute("SELECT repo, number, branch FROM prs")}
    by_branch = collections.defaultdict(list)
    for k, b in pr_branch.items():
        by_branch[b].append(k)
    owners = collections.defaultdict(list)
    for k in pr_branch:
        owners[int(k.rsplit("#", 1)[1])].append(k)
    unique_num = {n: ks[0] for n, ks in owners.items() if len(ks) == 1}  # bare #N only if unambiguous

    merged = {}
    if use_t:
        for s in load_transcripts(con):
            s["source"] = "transcript"
            merged[s["sid"]] = s
    if use_d:
        extra = hints = 0
        for s in load_digests():
            if s["sid"] in merged:
                # transcript wins, but the digest records where the session *ended* (the capture
                # hook reads the live branch), which a transcript can miss — add it as a hint
                t_br = merged[s["sid"]]["branches"]
                for b, v in s["branches"].items():
                    if b not in t_br:
                        t_br[b] = [max(v[0], 1), v[1]]
                        merged[s["sid"]]["hint_" + b] = True
                        hints += 1
                continue
            s["source"] = "digest"
            s["mentions"] = mentions_from_file(s["transcript"], pr_branch, unique_num)
            merged[s["sid"]] = s
            extra += 1
        print(f"  digests: {extra} sessions not in transcripts (Codex, or transcript deleted), "
              f"{hints} end-of-session branch hints added to transcript sessions", file=sys.stderr)

    for s in merged.values():
        cwd, main_cwd = s["cwd"], s.get("main_cwd") or s["cwd"]
        wt = is_worktree(main_cwd)
        br = s["branches"]
        total = sum(v[0] for v in br.values()) or 1
        main_br = max(br, key=lambda b: br[b][0]) if br else ""
        pm = resolve_mentions(s["mentions"], pr_branch, unique_num)
        survey = int(len(pm) > SURVEY_LIMIT)
        con.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            s["sid"], s["harness"], s["source"], rel_folder(main_cwd), cwd, wt, main_br, json.dumps(br),
            s["started"], s["last_seen"], s["tool_events"], s["title"], s["resumable"], survey,
            resume_cmd(s["harness"], s["sid"], cwd)))
        for b, (n, n_wt) in br.items():
            if n / total < BRANCH_SHARE and b != main_br and not s.get("hint_" + b):
                continue
            ev = "branch" if n_wt * 2 >= n else "branch-checkout"
            con.execute("INSERT INTO links VALUES (?,?,?,?,?)", (s["sid"], "branch", b, ev, n))
            for k in by_branch.get(b, []):
                con.execute("INSERT INTO links VALUES (?,?,?,?,?)", (s["sid"], "pr", k, ev, 1000))
            for t in TICKET_RE.findall(b.upper()):
                con.execute("INSERT INTO links VALUES (?,?,?,?,?)", (s["sid"], "ticket", t, "branch", 1000))
        if not survey:
            for k, c in pm.most_common(MENTION_TOP):
                if c < MENTION_MIN:
                    break
                con.execute("INSERT INTO links VALUES (?,?,?,?,?)", (s["sid"], "pr", k, "mention", c))
            for t, c in collections.Counter((s["mentions"] or {}).get("tickets", {})).most_common(10):
                con.execute("INSERT INTO links VALUES (?,?,?,?,?)", (s["sid"], "ticket", t, "mention", c))
    con.commit()
    write_md(con)
    n_l = con.execute("SELECT count(DISTINCT key) FROM links WHERE kind='pr'").fetchone()[0]
    src = dict(con.execute("SELECT source, count(*) FROM sessions GROUP BY source").fetchall())
    print(f"{len(merged)} sessions ({', '.join(f'{v} {k}' for k, v in src.items())}), "
          f"{len(pr_branch)} PRs, {n_l} PRs linked to ≥1 session → {DB}")


def write_md(con):
    q = """
    SELECT p.repo, p.number, p.title, p.state, p.created, p.merged, p.url, p.branch,
           s.sid, s.folder, s.worktree, s.last_seen, s.tool_events, s.resumable, s.resume,
           group_concat(DISTINCT l.evidence), max(l.weight), s.title
    FROM prs p JOIN links l ON l.kind='pr' AND l.key = p.repo || '#' || p.number
    JOIN sessions s ON s.sid = l.sid
    GROUP BY p.repo, p.number, s.sid
    ORDER BY p.created DESC, s.last_seen DESC"""
    groups = collections.OrderedDict()
    for r in con.execute(q):
        groups.setdefault(r[:8], []).append(r[8:])
    roots = ", ".join(f"`{r.replace(os.path.expanduser('~'), '~')}`" for r in CFG.code_roots)
    L = ["# Sessions index", "",
         "> Generated by `session_index.py` — don't edit by hand, rerun it. Source of truth: the sqlite next to it.", "",
         "Each PR lists the sessions that worked on it, strongest evidence first. "
         "**Evidence:** `branch` = worktree session on the PR's branch (reliable) · "
         "`branch-checkout` = primary checkout happened to be on it (weak) · "
         "`mention` = transcript names the PR ≥3×. ✗ = transcript gone, can't resume.", "",
         f"Folders are relative to the parent of {roots}.", ""]
    rank = {"branch": 0, "mention": 1, "branch-checkout": 2}
    for (repo, num, title, state, created, merged, url, branch), ss in groups.items():
        ss.sort(key=lambda x: (min(rank.get(e, 3) for e in x[7].split(",")), -(x[8] or 0), -(x[4] or 0)))
        L.append(f"### [{repo} #{num}]({url}) — {title}")
        L.append(f"`{state}` · opened {created}" + (f" · merged {merged}" if merged else "") + f" · branch `{branch}`")
        L.append("")
        L.append("| Session | Folder | Last seen | Events | Evidence | Resume |")
        L.append("|---|---|---|---:|---|---|")
        for sid, folder, wt, last, ev, ok, cmd, evid, _, stitle in ss[:6]:
            name = f"`{sid[:8]}`" + (f" {stitle[:50]}" if stitle else "")
            L.append(f"| {name} | `{folder}`{' 🌿' if wt else ''} | {last[:10]} | {ev} | {evid} | "
                     + (f"`{cmd}`" if ok else "✗") + " |")
        if len(ss) > 6:
            L.append(f"| … | {len(ss)-6} more — `session_index.py --pr {num} --repo {repo}` | | | | |")
        L.append("")
        L.append("")
    orphan = con.execute("""
        SELECT l.key, max(s.last_seen), sum(s.tool_events), count(DISTINCT s.sid),
               (SELECT x.resume FROM sessions x JOIN links y ON y.sid = x.sid
                 WHERE y.kind='branch' AND y.key = l.key AND x.resumable = 1
                 ORDER BY x.last_seen DESC LIMIT 1),
               (SELECT x.folder FROM sessions x JOIN links y ON y.sid = x.sid
                 WHERE y.kind='branch' AND y.key = l.key ORDER BY x.last_seen DESC LIMIT 1)
        FROM links l JOIN sessions s ON s.sid = l.sid
        WHERE l.kind='branch' AND s.worktree = 1 AND l.key NOT IN (SELECT branch FROM prs)
          AND s.last_seen >= date('now','-45 days')
        GROUP BY l.key ORDER BY max(s.last_seen) DESC""").fetchall()
    if orphan:
        L[8:8] = ["## Branches with no PR (worktree sessions, last 45 days)", "",
                  "| Branch | Folder | Last seen | Events | Sessions | Resume latest |",
                  "|---|---|---|---:|---:|---|"] + [
            f"| `{b}` | `{f}` | {ls[:10]} | {ev} | {n} | " + (f"`{cmd}`" if cmd else "✗") + " |"
            for b, ls, ev, n, cmd, f in orphan] + ["", "## By PR", ""]
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
        SELECT s.last_seen, s.tool_events, group_concat(DISTINCT l.evidence), s.resumable, s.resume, s.title,
               min(CASE l.evidence WHEN 'branch' THEN 0 WHEN 'mention' THEN 1 ELSE 2 END) AS r,
               max(CASE l.evidence WHEN 'mention' THEN l.weight ELSE 0 END) AS w
        FROM links l JOIN sessions s ON s.sid=l.sid WHERE l.kind=? AND l.key=?
        GROUP BY s.sid ORDER BY r, w DESC, s.tool_events DESC""", (kind, key)).fetchall()
    if not rows and kind == "branch":
        # no session ran on this branch — find sessions whose transcripts mention it
        paths = [r[0] for r in con.execute("SELECT path FROM tcache")]
        hits = []
        for i in range(0, len(paths), 200):
            r = subprocess.run(["grep", "-c", "-F", "--", args.branch, *paths[i:i + 200]],
                               capture_output=True, text=True)
            hits += [(int(c), p) for p, c in (l.rsplit(":", 1) for l in r.stdout.splitlines()) if c != "0"]
        sess = {s: (ls, ev, ok, cmd, ti) for s, ls, ev, ok, cmd, ti in con.execute(
            "SELECT sid, last_seen, tool_events, resumable, resume, title FROM sessions")}
        out = []
        for c, p in sorted(hits, reverse=True):
            sid = os.path.basename(p)[:-6]
            if sid in sess:
                ls, ev, ok, cmd, ti = sess[sid]
                out.append((ls, ev, f"mentions ×{c}", ok, cmd, ti))
        if out:
            print(f"no session ran on {key}; sessions that mention it:")
            for last, ev, evid, ok, cmd, title in out[:8]:
                print(f"{last[:16]}  {ev:>5} ev  {evid:<22} {(title or '')[:40]:<40}  "
                      + (cmd if ok else "(transcript gone) " + cmd))
            return
    if not rows:
        sys.exit(f"no sessions linked to {key}")
    for last, ev, evid, ok, cmd, title, _, _ in rows:
        print(f"{last[:16]}  {ev:>5} ev  {evid:<22} {(title or '')[:40]:<40}  "
              + (cmd if ok else "(transcript gone) " + cmd))


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
