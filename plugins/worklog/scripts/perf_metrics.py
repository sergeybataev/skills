#!/usr/bin/env python3
"""Per-month delivery metrics: merged PRs + time-to-merge (every configured org, configured gh user),
unique commits authored, commits per merged PR, ticket coverage, commit types/scopes.

  perf_metrics.py --months 6 [--json]        settings from ~/.config/worklog/config.toml
"""
import argparse, collections, dataclasses, datetime as dt, json, os, re, statistics, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wlconfig  # noqa: E402

CFG = wlconfig.load()
TICKET = CFG.ticket_re
CONV = re.compile(r"^(\w+)(?:\(([^)]*)\))?!?:")


def sh(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


@dataclasses.dataclass
class Month:
    merged: int = 0
    ttm: list = dataclasses.field(default_factory=list)
    commits: set = dataclasses.field(default_factory=set)
    ticketed: int = 0
    types: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    scopes: collections.Counter = dataclasses.field(default_factory=collections.Counter)


def month(d):
    return d[:7]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if sh(["gh", "api", "user", "-q", ".login"]).strip() != CFG.github_user:
        sys.exit(f"gh is not {CFG.github_user} — run: gh auth switch --user {CFG.github_user}")
    today = dt.date.today()
    y, mo = today.year, today.month - (a.months - 1)
    while mo <= 0:
        y, mo = y - 1, mo + 12
    first = dt.date(y, mo, 1)
    since = first.isoformat()

    prs = []
    for org in CFG.github_orgs:
        prs += json.loads(sh(["gh", "search", "prs", "--author", CFG.github_user, "--owner", org,
                              "--merged", "--merged-at", f">={since}", "--limit", "1000",
                              "--json", "repository,number,createdAt,closedAt"]) or "[]")
    m = collections.defaultdict(Month)
    for p in prs:
        c, x = p["createdAt"][:10], p["closedAt"][:10]
        b = m[month(x)]
        b.merged += 1
        b.ttm.append((dt.date.fromisoformat(x) - dt.date.fromisoformat(c)).days)

    seen = set()
    for repo in CFG.git_repos():
        out = sh(["git", "log", "--all", "--no-merges", *[f"--author={e}" for e in CFG.commit_emails],
                  f"--since={since} 00:00:00", "--pretty=%ad\x1f%s", "--date=short"], cwd=repo)
        for line in out.splitlines():
            d, s = line.split("\x1f", 1)
            if s.startswith(("index on ", "untracked files on ", "WIP on ")) or s in seen:
                continue  # stash pseudo-commits; same subject on several branches after rebase
            seen.add(s)
            b = m[month(d)]
            b.commits.add(s)
            b.ticketed += bool(TICKET.search(s))
            cm = CONV.match(s)
            if cm:
                b.types[cm.group(1)] += 1
                if cm.group(2):
                    b.scopes[cm.group(2)] += 1

    rows = []
    for k in sorted(k for k in m if k >= since[:7]):
        b = m[k]
        n = len(b.commits)
        t = b.ttm
        rows.append({
            "month": k, "merged": b.merged,
            "ttm_median": statistics.median(t) if t else None,
            "ttm_mean": round(statistics.mean(t), 1) if t else None,
            "ttm_max": max(t) if t else None,
            "commits": n,
            "commits_per_merged_pr": round(n / b.merged, 1) if b.merged else None,
            "ticket_coverage_pct": round(100 * b.ticketed / n) if n else None,
            "top_types": b.types.most_common(5),
            "top_scopes": b.scopes.most_common(5),
        })
    if a.json:
        print(json.dumps(rows, indent=2))
        return
    print(f"{'month':8} {'merged':>6} {'ttm med':>7} {'mean':>5} {'max':>4} {'commits':>7} {'c/PR':>5} {'ticket%':>7}  top scopes")
    for r in rows:
        f = lambda v: "-" if v is None else v
        print(f"{r['month']:8} {r['merged']:>6} {f(r['ttm_median']):>7} {f(r['ttm_mean']):>5} {f(r['ttm_max']):>4} "
              f"{r['commits']:>7} {f(r['commits_per_merged_pr']):>5} {f(r['ticket_coverage_pct']):>7}  "
              + ", ".join(f"{s}={n}" for s, n in r["top_scopes"][:3]))
    print(f"\n{len(prs)} merged PRs since {since}; current month is partial.")


if __name__ == "__main__":
    main()
