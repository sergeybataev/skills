#!/usr/bin/env python3
"""Reviews and comments you gave on other people's PRs, within a window.

Reads the local store (ghcache.py), refreshing it incrementally first: every review and
comment you left on someone else's PR, with its own timestamp, so only activity inside the
window counts — not PRs that merely got updated in it.

  reviews.py --since 2026-09-01 [--until 2026-09-30] [--json] [--response-time]

--response-time adds hours from "review requested from you" to your next review
(one extra timeline call per PR). Defaults to features.review_response_time.
"""
import argparse, collections, datetime as dt, json, os, statistics, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wlconfig  # noqa: E402

def collect(cfg, since, until, response_time=None):
    """Your reviews/comments on others' PRs inside the window, read from the local store."""
    import ghcache
    if response_time is None:
        response_time = cfg.on("review_response_time")
    con, _ = ghcache.refresh(prs=False)
    rows = [r for r in con.execute("SELECT kind, repo, number, state, ts FROM my_reviews WHERE ts IS NOT NULL")
            if cfg.in_window(r[4], since, until)]
    by_pr = collections.defaultdict(list)
    for kind, repo, num, state, ts in rows:
        by_pr[(repo, num)].append((kind, state, ts))
    out = []
    for (repo, num), acts in by_pr.items():
        meta = con.execute("SELECT title, author, state, merged, url FROM reviewed_prs WHERE repo=? AND number=?",
                           (repo, num)).fetchone() or ("", "", "", None, f"https://github.com/{repo}/pull/{num}")
        reviews = sorted((ts, st) for k, st, ts in acts if k == "review")
        rec = {
            "repo": repo, "number": num, "title": meta[0], "author": meta[1],
            "pr_state": "merged" if meta[3] else meta[2], "url": meta[4],
            "verdict": reviews[-1][1] if reviews else "COMMENTED",
            "reviews": len(reviews),
            "inline_comments": sum(1 for k, _, _ in acts if k == "inline"),
            "conversation_comments": sum(1 for k, _, _ in acts if k == "convo"),
            "first": min(ts for _, _, ts in acts)[:10], "last": max(ts for _, _, ts in acts)[:10],
            "response_hours": None,
        }
        if response_time and reviews:
            first = reviews[0][0]
            req = con.execute("SELECT max(ts) FROM review_requests WHERE repo=? AND number=? AND ts<=?",
                              (repo, num, first)).fetchone()[0]
            if req:
                d = (dt.datetime.fromisoformat(first.replace("Z", "+00:00"))
                     - dt.datetime.fromisoformat(req.replace("Z", "+00:00")))
                rec["response_hours"] = round(d.total_seconds() / 3600, 1)
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
