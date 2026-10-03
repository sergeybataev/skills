#!/usr/bin/env python3
"""Your PRs in a window, open-PR review state with findings, and unpushed branches.

  prs.py --since "2026-10-02 19:40" [--until 2026-10-03] [--json] [--no-unpushed]

Review state follows the rules that keep advice honest:
  approval      a human's *latest* review is APPROVED (bots and your own reviews ignored;
                a later CHANGES_REQUESTED overrides an earlier approval)
  needs more    approved, but reviewDecision is still REVIEW_REQUIRED (second approver / code owner)
  stacked       base is neither a base branch nor an integration branch from the config
  no reviewers  no pending requests AND no human reviews
Unpushed      local branches with commits on no remote branch, minus branches whose PR merged.
"""
import argparse, concurrent.futures as cf, datetime as dt, fnmatch, json, os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wlconfig  # noqa: E402

CFG = wlconfig.load()
U = CFG.github_user


def sh(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def gj(cmd):
    out = sh(cmd)
    return json.loads(out) if out else []


def is_bot(login):
    return login.endswith("[bot]") or login in ("github-actions", "copilot")


def search(org, *flags):
    # Sorted by update time and filtered client-side: GitHub's --closed/--merged-at qualifiers
    # read a search index that lags, and silently missed a PR closed the same day.
    return gj(["gh", "search", "prs", "--author", U, "--owner", org, *flags, "--sort", "updated",
               "--limit", "300", "--json", "repository,number,title,createdAt,closedAt,isDraft,url"])


def open_state(r):
    repo, n = r["repository"]["nameWithOwner"], r["number"]
    v = gj(["gh", "pr", "view", str(n), "-R", repo, "--json",
            "isDraft,reviewRequests,reviews,reviewDecision,mergeable,baseRefName,headRefName"])
    latest = {}
    for x in sorted(v.get("reviews", []), key=lambda x: x.get("submittedAt") or ""):
        a = x["author"]["login"]
        if a == U or is_bot(a) or x["state"] in ("PENDING",):
            continue
        if x["state"] == "COMMENTED" and latest.get(a) in ("APPROVED", "CHANGES_REQUESTED"):
            continue  # a later comment doesn't withdraw a verdict
        latest[a] = x["state"]
    approvers = sorted(a for a, s in latest.items() if s == "APPROVED")
    changers = sorted(a for a, s in latest.items() if s == "CHANGES_REQUESTED")
    pending = len(v.get("reviewRequests", []))
    base = v.get("baseRefName", "")
    stacked = base not in set(CFG.base_branches) | set(CFG.integration_branches)
    findings = []
    scratch = any(fnmatch.fnmatch(v.get("headRefName", ""), p) for p in CFG.scratch_branches) or \
        any(s in r["title"].lower() for s in ("never merge", "do not merge", "don't merge"))
    if scratch:
        findings.append("scratch")
    if changers:
        findings.append("changes-requested")
    if approvers and not changers:
        findings.append("approved-but-draft" if v["isDraft"]
                        else "approved-needs-more" if v.get("reviewDecision") == "REVIEW_REQUIRED"
                        else "approved")
    if not pending and not latest and not scratch:
        findings.append("no-reviewers")
    if stacked:
        findings.append(f"stacked-on:{base}")
    if v.get("mergeable") == "CONFLICTING":
        findings.append("conflicting")
    if latest and not approvers and not changers:
        findings.append("reviewed-undecided")
    age = (CFG.now().date() - dt.date.fromisoformat(r["createdAt"][:10])).days
    return {"repo": repo, "number": n, "title": r["title"], "url": r["url"], "age_days": age,
            "draft": v["isDraft"], "base": base, "head": v.get("headRefName", ""),
            "pending_reviewers": pending, "human_reviewers": len(latest),
            "approvers": approvers, "changes_requested_by": changers,
            "decision": v.get("reviewDecision") or "", "mergeable": v.get("mergeable", ""),
            "findings": findings}


def unpushed(days=30):
    """Branches touched in the last `days` with commits on no remote — old local clutter is ignored."""
    out, scratch = [], []
    cutoff = (CFG.now() - dt.timedelta(days=days)).isoformat()
    for repo in CFG.git_repos():
        sh(["git", "fetch", "--quiet", "origin"], cwd=repo)
        refs = sh(["git", "for-each-ref", "--format=%(refname:short)\t%(committerdate:iso-strict)", "refs/heads"], cwd=repo)
        for line in refs.splitlines():
            b, when = line.split("\t")
            if dt.datetime.fromisoformat(when) < dt.datetime.fromisoformat(cutoff):
                continue
            n = int(sh(["git", "rev-list", "--count", b, "--not", "--remotes"], cwd=repo) or 0)
            own = sh(["git", "ls-remote", "--heads", "origin", b], cwd=repo)
            if not n and own:
                continue
            if not n:
                # every commit is on some remote, but not on a branch of this name — e.g. merged into
                # a throwaway branch. Backed up, but not pushed where anyone would look for it.
                base = sh(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=repo) or "origin/main"
                n = int(sh(["git", "rev-list", "--count", b, "--not", base], cwd=repo) or 0)
                if not n:
                    continue
                kind = "no remote branch of its own (commits only reachable via other branches)"
            else:
                kind = "commits on no remote"
            if any(fnmatch.fnmatch(b, pat) for pat in CFG.scratch_branches):
                scratch.append(b)
                continue
            r = subprocess.run(["gh", "pr", "list", "--head", b, "--state", "merged", "--json", "number"],
                               cwd=repo, capture_output=True, text=True)
            if r.returncode == 0 and json.loads(r.stdout or "[]"):
                continue  # squash-merged branch: its commits live on in the merge
            out.append({"repo": os.path.basename(repo), "branch": b, "unpushed": n, "kind": kind,
                        "last_commit": when[:16]})
    return sorted(out, key=lambda x: x["last_commit"], reverse=True), scratch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-unpushed", action="store_true")
    ap.add_argument("--unpushed-days", type=int, default=30)
    a = ap.parse_args()
    until = a.until or CFG.now().strftime("%Y-%m-%d %H:%M")
    if not CFG.on("prs"):
        sys.exit("prs are disabled in [features]")
    if sh(["gh", "api", "user", "-q", ".login"]) != U:
        sys.exit(f"gh is not {U} — run: gh auth switch --user {U}")

    merged, closed, opened = [], [], []
    for org in CFG.github_orgs:
        m = search(org, "--merged")
        mk = {(r["repository"]["nameWithOwner"], r["number"]) for r in m}
        merged += [r for r in m if CFG.in_window(r["closedAt"], a.since, until)]
        closed += [r for r in search(org, "--state", "closed")
                   if CFG.in_window(r["closedAt"], a.since, until)
                   and (r["repository"]["nameWithOwner"], r["number"]) not in mk
                   and not gj(["gh", "pr", "view", str(r["number"]), "-R", r["repository"]["nameWithOwner"],
                               "--json", "mergedAt"]).get("mergedAt")]
        opened += search(org, "--state", "open")
    with cf.ThreadPoolExecutor(8) as ex:
        states = sorted(ex.map(open_state, opened), key=lambda s: -s["age_days"])
    up, scratch = ([], []) if a.no_unpushed else unpushed(a.unpushed_days)

    res = {"since": a.since, "until": until,
           "merged": [{"repo": r["repository"]["nameWithOwner"], "number": r["number"], "title": r["title"],
                       "url": r["url"], "merged_at": r["closedAt"]} for r in sorted(merged, key=lambda r: r["closedAt"])],
           "closed_unmerged": [{"repo": r["repository"]["nameWithOwner"], "number": r["number"], "title": r["title"],
                                "closed_at": r["closedAt"]} for r in closed],
           "open": states, "unpushed": up, "scratch_unpushed": scratch}
    if a.json:
        print(json.dumps(res, indent=2))
        return
    print(f"# PRs — {a.since} → {until}\n")
    print(f"## Merged ({len(res['merged'])})")
    for r in res["merged"]:
        print(f"- {r['merged_at'][:16]} `{r['repo']}` [#{r['number']}]({r['url']}) — {r['title']}")
    if res["closed_unmerged"]:
        print(f"\n## Closed unmerged ({len(closed)})")
        for r in res["closed_unmerged"]:
            print(f"- {r['closed_at'][:10]} `{r['repo']}` #{r['number']} — {r['title']}")
    print(f"\n## Open ({len(states)})\n")
    print("| Age | PR | State | Reviewers | Findings |\n|---:|---|---|---|---|")
    for s in states:
        rv = f"{s['pending_reviewers']} pending · {s['human_reviewers']} reviewed" + \
             (f" · ✔ {', '.join(s['approvers'])}" if s["approvers"] else "") + \
             (f" · ✘ {', '.join(s['changes_requested_by'])}" if s["changes_requested_by"] else "")
        print(f"| {s['age_days']}d | `{s['repo'].split('/')[-1]}` [#{s['number']}]({s['url']}) {s['title'][:55]} | "
              f"{'draft' if s['draft'] else 'ready'} | {rv} | {', '.join(s['findings']) or '—'} |")
    if up:
        print(f"\n## Unpushed branches ({len(up)}) — touched in the last {a.unpushed_days} days, no merged PR\n")
        for u in up:
            print(f"- `{u['repo']}` `{u['branch']}` — {u['unpushed']} commit(s), last {u['last_commit']} · {u['kind']}")
    if scratch:
        print(f"\n({len(scratch)} scratch branches with local-only commits ignored — identity.scratch_branches)")


if __name__ == "__main__":
    main()
