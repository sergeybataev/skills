#!/usr/bin/env python3
"""Reviews and comments you gave on other people's PRs, within a window.

Finds candidate PRs with GitHub search (reviewed-by / commenter, excluding your own
PRs), then reads each PR's reviews and comments and keeps only *your* activity whose
timestamp falls in the window — search alone filters on the PR's update time, which
moves whenever anyone touches it.

  reviews.py --since 2026-09-01 [--until 2026-09-30] [--json] [--response-time]

--response-time adds hours from "review requested from you" to your next review
(one extra timeline call per PR). Defaults to features.review_response_time.
"""
import argparse, collections, concurrent.futures as cf, datetime as dt, json, os, statistics, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wlconfig  # noqa: E402

BOTS = ("[bot]",)


def gh(path, paginate=True, **params):
    cmd = ["gh", "api", "-X", "GET", path] + (["--paginate"] if paginate else [])
    for k, v in params.items():
        cmd += ["-f", f"{k}={v}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh api {path}: {r.stderr.strip()[:200]}")
    out = r.stdout.strip()
    if not out:
        return []
    # --paginate concatenates JSON arrays/objects back to back
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return json.loads("[" + out.replace("][", "],[").replace("}{", "},{") + "]")


def search(cfg, since):
    """Candidate PRs: {(repo, number): search item}."""
    found = {}
    u = cfg.github_user
    for org in cfg.github_orgs:
        for qual in ("reviewed-by", "commenter"):
            q = f"org:{org} is:pr {qual}:{u} -author:{u} updated:>={since}"
            for page in range(1, 11):  # search API caps at 1000 results
                res = gh("search/issues", paginate=False, q=q, per_page=100, page=page)
                items = res.get("items", [])
                for it in items:
                    repo = it["repository_url"].split("/repos/", 1)[1]
                    found[(repo, it["number"])] = it
                if len(items) < 100:
                    break
    return found


def in_window(ts, since, until):
    return bool(ts) and since <= ts[:10] <= until


def one_pr(cfg, repo, num, item, since, until, response_time):
    u = cfg.github_user
    reviews = [r for r in gh(f"repos/{repo}/pulls/{num}/reviews")
               if r["user"]["login"] == u and in_window(r.get("submitted_at"), since, until)]
    inline = [c for c in gh(f"repos/{repo}/pulls/{num}/comments")
              if c["user"]["login"] == u and in_window(c.get("created_at"), since, until)]
    convo = [c for c in gh(f"repos/{repo}/issues/{num}/comments")
             if c["user"]["login"] == u and in_window(c.get("created_at"), since, until)]
    if not (reviews or inline or convo):
        return None
    states = [r["state"] for r in sorted(reviews, key=lambda r: r["submitted_at"])]
    rec = {
        "repo": repo, "number": num, "title": item["title"],
        "author": item["user"]["login"],
        "pr_state": "merged" if (item.get("pull_request") or {}).get("merged_at") else item["state"],
        "url": item["html_url"],
        "verdict": states[-1] if states else "COMMENTED",
        "reviews": len(reviews), "inline_comments": len(inline), "conversation_comments": len(convo),
        "first": min([r["submitted_at"] for r in reviews] + [c["created_at"] for c in inline + convo])[:10],
        "last": max([r["submitted_at"] for r in reviews] + [c["created_at"] for c in inline + convo])[:10],
        "response_hours": None,
    }
    if response_time and reviews:
        events = gh(f"repos/{repo}/issues/{num}/timeline")
        reqs = sorted(e["created_at"] for e in events
                      if e.get("event") == "review_requested"
                      and (e.get("requested_reviewer") or {}).get("login") == u)
        first_review = min(r["submitted_at"] for r in reviews)
        before = [t for t in reqs if t <= first_review]
        if before:
            d = (dt.datetime.fromisoformat(first_review.replace("Z", "+00:00"))
                 - dt.datetime.fromisoformat(before[-1].replace("Z", "+00:00")))
            rec["response_hours"] = round(d.total_seconds() / 3600, 1)
    return rec


def collect(cfg, since, until, response_time=None):
    if response_time is None:
        response_time = cfg.on("review_response_time")
    cands = search(cfg, since)
    out = []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(one_pr, cfg, r, n, it, since, until, response_time)
                for (r, n), it in cands.items()]
        for f in cf.as_completed(futs):
            rec = f.result()
            if rec:
                out.append(rec)
    return sorted(out, key=lambda r: (r["last"], r["repo"], r["number"]), reverse=True)


def summary(recs):
    v = collections.Counter(r["verdict"] for r in recs)
    rt = [r["response_hours"] for r in recs if r["response_hours"] is not None]
    return {
        "prs_reviewed": len(recs),
        "authors": len({r["author"] for r in recs}),
        "approved": v.get("APPROVED", 0),
        "changes_requested": v.get("CHANGES_REQUESTED", 0),
        "commented_only": v.get("COMMENTED", 0),
        "dismissed": v.get("DISMISSED", 0),  # your approval was dismissed, usually by new commits
        "inline_comments": sum(r["inline_comments"] for r in recs),
        "conversation_comments": sum(r["conversation_comments"] for r in recs),
        "median_response_hours": statistics.median(rt) if rt else None,
        "response_samples": len(rt),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default=dt.date.today().isoformat())
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--response-time", action="store_true", default=None)
    a = ap.parse_args()
    cfg = wlconfig.load()
    if not cfg.on("reviews"):
        sys.exit("reviews are disabled in [features] — set reviews = true to collect them")
    who = subprocess.run(["gh", "api", "user", "-q", ".login"], capture_output=True, text=True).stdout.strip()
    if who != cfg.github_user:
        sys.exit(f"gh is '{who}', not {cfg.github_user} — run: gh auth switch --user {cfg.github_user}")
    recs = collect(cfg, a.since, a.until, a.response_time)
    s = summary(recs)
    if a.json:
        print(json.dumps({"summary": s, "prs": recs}, indent=2))
        return
    print(f"# Reviews given — {a.since} → {a.until}\n")
    print(f"**{s['prs_reviewed']} PRs** by {s['authors']} people · {s['approved']} approved · "
          f"{s['changes_requested']} changes requested · {s['commented_only']} comment-only · "
          + (f"{s['dismissed']} dismissed · " if s["dismissed"] else "")
          + f"{s['inline_comments']} inline + {s['conversation_comments']} conversation comments"
          + (f" · median response {s['median_response_hours']} h ({s['response_samples']} PRs)"
             if s["median_response_hours"] is not None else "") + "\n")
    print("| Last | PR | Author | Verdict | Inline | Convo | PR state |")
    print("|---|---|---|---|---:|---:|---|")
    for r in recs:
        print(f"| {r['last']} | [{r['repo']} #{r['number']}]({r['url']}) — {r['title'][:60]} | "
              f"{r['author']} | {r['verdict']} | {r['inline_comments']} | {r['conversation_comments']} | {r['pr_state']} |")


if __name__ == "__main__":
    main()
