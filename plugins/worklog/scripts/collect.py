#!/usr/bin/env python3
"""
worklog-collect — build a personal work log from what you actually did.

Sources, in order of signal:
  1. git commits   — every repo under the configured code roots, all branches
                     (worktrees share the object store, so --all covers them)
  2. GitHub        — PRs you opened / merged / reviewed, via `gh` (optional)
  3. claude sessions — llm-wiki session digests, for which repo you were in when
                     (metadata only; digest bodies are redacted by design)

Output is a REVIEW DRAFT, not a commit. Origin tags (#initiative / #assigned)
are guessed from whether a commit references a ticket, and the guess is wrong
often enough that it needs your eye before it lands in the tracker.

Usage
  worklog-collect.py                      # since last tracker entry (default)
  worklog-collect.py --since 2026-08-03
  worklog-collect.py --since 2026-08-03 --until 2026-08-10
  worklog-collect.py --days 7
  worklog-collect.py --week                # current Sun-Thu work week
  worklog-collect.py --no-github           # git only, offline
  worklog-collect.py --no-reviews          # skip reviews you gave (else on if features.reviews)

Each source also follows its [features] switch in the config (prs, reviews, commits, sessions).
  worklog-collect.py --raw                 # every commit, no roll-up
  worklog-collect.py --out draft.md        # write instead of stdout

Settings come from ~/.config/worklog/config.toml (run /worklog:setup).
Nothing is ever written to the tracker automatically — paste the reviewed
block in yourself, which is also the moment you decide the tags.
"""

import argparse
import collections
import datetime as dt
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wlconfig  # noqa: E402

CFG = wlconfig.load()
TRACKER = CFG.p.get("tracker", "")
DIGESTS = CFG.digests
TICKET_RE = re.compile("(" + CFG.ticket_re.pattern + ")", re.I)

# conventional commit: type(scope): subject
CONV_RE = re.compile(r"^(?P<type>\w+)(?:\((?P<scope>[^)]*)\))?!?:\s*(?P<subject>.*)$")

# commit types that are usually plumbing rather than reportable work
NOISE_TYPES = {"style", "chore"}


def run(cmd, cwd=None):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def git_repos():
    """Top-level git repos under the code roots; worktrees share the parent's --all."""
    return CFG.git_repos()


def collect_commits(since, until, lookback_days=120):
    """({date: [commit,...]} authored in window, [commits landed in window but
    authored earlier]).

    git --since/--until filter on COMMITTER date, but a worklog wants to know when
    the work was *written*. Those differ whenever you rebase or cherry-pick — 10 of
    49 commits in one real window. So: fetch a wide committer-date net, then split
    on author date. Work authored earlier but landed now is reported separately
    rather than silently dated to the day it was rebased.
    """
    seen = set()
    by_day = collections.defaultdict(list)
    landed_earlier = []
    wide = (dt.date.fromisoformat(since) - dt.timedelta(days=lookback_days)).isoformat()
    for repo in git_repos():
        repo_name = os.path.basename(repo)
        fmt = "%H%x1f%ad%x1f%cd%x1f%s"
        out = run(
            ["git", "log", "--all", "--no-merges", *[f"--author={e}" for e in CFG.commit_emails],
             # NB: a bare date makes git approxidate fill in the CURRENT time,
             # so "--since=2026-08-10" means "since 22:07 today". Always pin the clock.
             f"--since={wide} 00:00:00", f"--until={until} 23:59:59",
             f"--pretty={fmt}", "--date=short"],
            cwd=repo,
        )
        for line in out.splitlines():
            parts = line.split("\x1f")
            if len(parts) < 4:
                continue
            sha, adate, cdate, subject = parts[0], parts[1], parts[2], parts[3]
            if sha in seen:
                continue
            seen.add(sha)
            m = CONV_RE.match(subject)
            rec = {
                "sha": sha[:10],
                "repo": repo_name,
                "subject": subject,
                "adate": adate,
                "cdate": cdate,
                "type": (m.group("type") if m else "").lower(),
                "scope": (m.group("scope") or "") if m else "",
                "tickets": sorted({t.upper() for t in TICKET_RE.findall(subject)}),
            }
            if since <= adate <= until:
                by_day[adate].append(rec)
            elif since <= cdate <= until:
                landed_earlier.append(rec)
    return by_day, landed_earlier


def collect_github(since, until):
    """PRs opened / merged in range, plus reviews you left. Best effort."""
    ev = collections.defaultdict(list)
    if not run(["gh", "--version"]):
        return ev, "gh not installed"
    who = run(["gh", "api", "user", "-q", ".login"]).strip()
    if who != CFG.github_user:
        return ev, (f"gh is authenticated as '{who or 'nobody'}', not {CFG.github_user} — "
                    f"GitHub activity SKIPPED. Fix: gh auth switch --user {CFG.github_user}")
    for (state, field, label), org in [(s, o) for s in (("open", "createdAt", "opened"),
                                                       ("merged", "closedAt", "merged"))
                                       for o in CFG.github_orgs]:
        out = run(["gh", "search", "prs", "--author", CFG.github_user, "--owner", org,
                   f"--{'state' if state == 'open' else 'merged'}",
                   *( [state] if state == "open" else [] ),
                   "--limit", "80", "--json",
                   "repository,number,title,createdAt,closedAt"])
        try:
            for r in json.loads(out or "[]"):
                d = (r.get(field) or "")[:10]
                if d and since <= d <= until:
                    ev[d].append({
                        "kind": label,
                        "repo": r["repository"]["nameWithOwner"],
                        "number": r["number"],
                        "title": r["title"],
                    })
        except Exception:
            pass
    return ev, None


def collect_sessions(since, until):
    """{date: {cwd_basename: n}} from llm-wiki session digests (metadata only)."""
    out = collections.defaultdict(collections.Counter)
    if not DIGESTS or not os.path.isdir(DIGESTS):
        return out
    for root, _, files in os.walk(DIGESTS):
        for f in files:
            if not f.endswith(".md"):
                continue
            p = os.path.join(root, f)
            d = dt.date.fromtimestamp(os.path.getmtime(p)).isoformat()
            if not (since <= d <= until):
                continue
            head = ""
            try:
                with open(p, errors="ignore") as fh:
                    head = fh.read(4000)
            except Exception:
                continue
            m = re.search(r"^cwd:\s*(.+)$", head, re.M) or re.search(r"in (/[^\s`]+)", head)
            cwd = (m.group(1) if m else "?").strip().strip('"\'`,.').rstrip("/")
            if not CFG.owns_cwd(cwd):
                continue
            out[d][os.path.basename(cwd) or "?"] += 1
    return out


def last_tracker_date():
    try:
        txt = open(TRACKER).read()
    except OSError:
        return None
    ds = re.findall(r"^- \*\*(\d{4}-\d{2}-\d{2})", txt, re.M)
    return max(ds) if ds else None


def guess_tag(commits):
    """Ticket referenced anywhere in the day's work for that group => assigned."""
    return "#assigned" if any(c["tickets"] for c in commits) else "#initiative"


def rollup(commits):
    """Group a day's commits by (repo, scope) and summarise."""
    groups = collections.defaultdict(list)
    for c in commits:
        groups[(c["repo"], c["scope"] or c["type"] or "-")].append(c)
    return sorted(groups.items(), key=lambda kv: -len(kv[1]))


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--days", type=int)
    ap.add_argument("--week", action="store_true", help="current Sun–Thu work week")
    ap.add_argument("--no-github", action="store_true")
    ap.add_argument("--no-sessions", action="store_true")
    ap.add_argument("--no-reviews", action="store_true")
    ap.add_argument("--raw", action="store_true", help="list every commit")
    ap.add_argument("--today", help="override today (YYYY-MM-DD), for testing")
    ap.add_argument("--out")
    a = ap.parse_args()

    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    until = a.until or today.isoformat()

    if a.since:
        since = a.since
    elif a.days:
        since = (today - dt.timedelta(days=a.days)).isoformat()
    elif a.week:
        # work week starts on the configured first day. Python: Mon=0 … Sun=6
        first = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].index(CFG.work_week[0])
        back = (today.weekday() - first) % 7
        since = (today - dt.timedelta(days=back)).isoformat()
    else:
        lt = last_tracker_date()
        since = lt or (today - dt.timedelta(days=7)).isoformat()

    commits, landed = collect_commits(since, until) if CFG.on("commits") else ({}, [])
    if a.no_github or not CFG.on("prs"):
        gh, gh_note = {}, "PRs skipped (--no-github or features.prs = false)"
    else:
        gh, gh_note = collect_github(since, until)
    sessions = {} if (a.no_sessions or not CFG.on("sessions")) else collect_sessions(since, until)
    revs, rev_note = [], None
    if CFG.on("reviews") and not (a.no_github or a.no_reviews):
        import reviews
        who = run(["gh", "api", "user", "-q", ".login"]).strip()
        if who == CFG.github_user:
            try:
                revs = reviews.collect(CFG, since, until)
            except RuntimeError as e:
                rev_note = f"reviews failed: {e}"
        else:
            rev_note = f"reviews skipped — gh is '{who}', not {CFG.github_user}"

    L = []
    W = L.append
    W(f"# Work log draft — {since} → {until}")
    W("")
    ndays = len({d for d in list(commits) + list(gh)})
    W(f"**{sum(len(v) for v in commits.values())} commits** across "
      f"{len({c['repo'] for v in commits.values() for c in v})} repo(s) on {ndays} day(s).")
    if gh_note:
        W(f"")
        W(f"> ⚠ GitHub: {gh_note}")
    W("")
    W("> Review before pasting into `TODO_tracker.md`. Tags below are **guesses** — "
      "`#assigned` where a ticket is referenced, `#initiative` otherwise. That heuristic "
      "misses self-initiated work done on a ticketed branch, and mislabels chores. "
      "Fixing the tag is the one part worth doing by hand.")
    W("")

    for day in sorted(set(list(commits) + list(gh) + list(sessions)), reverse=True):
        W(f"## {day}")
        W("")
        for e in gh.get(day, []):
            W(f"- **PR {e['kind']}** — `{e['repo']}` [#{e['number']}]"
              f"(https://github.com/{e['repo']}/pull/{e['number']}) — *{e['title']}*")
        cs = commits.get(day, [])
        if cs:
            tickets = sorted({t for c in cs for t in c["tickets"]})
            if a.raw:
                for c in cs:
                    W(f"- `{c['sha']}` {c['repo']}: {c['subject']}")
            else:
                for (repo, scope), group in rollup(cs):
                    subs = [c["subject"] for c in group]
                    kinds = collections.Counter(c["type"] for c in group if c["type"])
                    kind_s = ", ".join(f"{k}×{n}" for k, n in kinds.most_common())
                    W(f"- **{repo} / {scope}** — {len(group)} commit(s)"
                      + (f" ({kind_s})" if kind_s else "") + f" {guess_tag(group)}")
                    for s_ in subs[:6]:
                        W(f"  - {s_}")
                    if len(subs) > 6:
                        W(f"  - …and {len(subs)-6} more")
            if tickets:
                W(f"- tickets touched: {', '.join(tickets)}")
            noise = sum(1 for c in cs if c["type"] in NOISE_TYPES)
            if noise:
                W(f"- ({noise} style/chore commit(s) included — usually not worth logging)")
        if day in sessions and sessions[day]:
            top = ", ".join(f"{k}×{v}" for k, v in sessions[day].most_common(3))
            W(f"- claude sessions: {top}")
        W("")

    if revs or rev_note:
        import reviews
        s = reviews.summary(revs)
        L.append("## Reviews given")
        L.append("")
        if rev_note:
            L.append(f"> ⚠ {rev_note}")
            L.append("")
        if revs:
            L.append(f"**{s['prs_reviewed']} PRs** by {s['authors']} people · {s['approved']} approved · "
                     f"{s['changes_requested']} changes requested · {s['commented_only']} comment-only · "
                     + (f"{s['dismissed']} dismissed · " if s["dismissed"] else "")
                     + f"{s['inline_comments']} inline comments"
                     + (f" · median response {s['median_response_hours']} h" if s["median_response_hours"] is not None else "")
                     + " · tag `#review`")
            L.append("")
            for r in revs:
                L.append(f"- **{r['last']}** — `{r['repo']}` [#{r['number']}]({r['url']}) — *{r['title']}* "
                         f"by {r['author']}: **{r['verdict'].replace('_', ' ').lower()}**"
                         + (f", {r['inline_comments']} inline comment(s)" if r["inline_comments"] else ""))
            L.append("")

    if landed:
        L.append("## Landed in this window, authored earlier")
        L.append("")
        L.append("Rebased or cherry-picked in. The work happened on the author date, "
                 "so it belongs to that week's log — listed here only so the merge isn't lost.")
        L.append("")
        for c in sorted(landed, key=lambda c: c["adate"], reverse=True):
            L.append(f"- `{c['sha']}` {c['repo']}: {c['subject']}  "
                     f"*(authored {c['adate']}, landed {c['cdate']})*")
        L.append("")

    text = "\n".join(L)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text + "\n")
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
