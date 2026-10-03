#!/usr/bin/env python3
"""Local SQLite store of your GitHub PRs and the reviews you gave, refreshed incrementally.

  ghcache.py refresh [--full]     # fetch what changed since the last refresh (or everything)
  ghcache.py stats                # what's stored and when it was last refreshed

The other scripts call refresh() themselves, so you rarely run this by hand. Each refresh
re-reads from one day before the last one: GitHub's search index lags, and a little overlap
is cheaper than a silently missed PR. Open-PR review *state* is not stored — it changes daily
and prs.py reads it live.

Database: paths.data_db (default: worklog.sqlite next to the sessions db).
"""
import argparse, concurrent.futures as cf, datetime as dt, json, os, sqlite3, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wlconfig  # noqa: E402

CFG = wlconfig.load()
U = CFG.github_user
OVERLAP = dt.timedelta(days=1)
FRESH = dt.timedelta(minutes=10)   # a refresh newer than this is reused, not repeated


def db_path():
    if "data_db" in CFG.p:
        return CFG.p["data_db"]
    base = os.path.dirname(CFG.p["sessions_db"]) if "sessions_db" in CFG.p else CFG.notes_root
    return os.path.join(base, "worklog.sqlite")


def connect():
    p = db_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    con = sqlite3.connect(p)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS meta(k PRIMARY KEY, v);
    CREATE TABLE IF NOT EXISTS my_prs(repo, number INT, title, state, created, closed, merged, updated, url,
        PRIMARY KEY(repo, number));
    CREATE TABLE IF NOT EXISTS reviewed_prs(repo, number INT, title, author, state, merged, updated, url,
        PRIMARY KEY(repo, number));
    CREATE TABLE IF NOT EXISTS my_reviews(kind, id, repo, number INT, state, ts, PRIMARY KEY(kind, id));
    CREATE TABLE IF NOT EXISTS review_requests(repo, number INT, ts, PRIMARY KEY(repo, number, ts));
    """)
    return con


def gh_api(path, **params):
    cmd = ["gh", "api", "-X", "GET", path]
    for k, v in params.items():
        cmd += ["-f", f"{k}={v}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh api {path}: {r.stderr.strip()[:200]}")
    return json.loads(r.stdout or "null")


def gh_list(path):
    """GET a paginated list endpoint; --paginate prints one JSON array per page back to back."""
    r = subprocess.run(["gh", "api", "-X", "GET", "--paginate", path], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh api {path}: {r.stderr.strip()[:200]}")
    dec, s, i, out = json.JSONDecoder(), r.stdout, 0, []
    while i < len(s):
        while i < len(s) and s[i].isspace():
            i += 1
        if i >= len(s):
            break
        page, i = dec.raw_decode(s, i)
        out += page if isinstance(page, list) else [page]
    return out


def search(q, cutoff):
    """Issues-search pages sorted by update time, stopping once results are older than cutoff."""
    items = []
    for page in range(1, 11):  # the search API stops at 1000 results
        res = gh_api("search/issues", q=q, sort="updated", order="desc", per_page=100, page=page)
        batch = res.get("items", [])
        items += batch
        if len(batch) < 100 or (cutoff and batch[-1]["updated_at"] < cutoff):
            break
    return [i for i in items if not cutoff or i["updated_at"] >= cutoff]


def repo_of(item):
    return item["repository_url"].split("/repos/", 1)[1]


def watermark(con, key, full):
    if full:
        return None
    v = con.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    if not v:
        return None
    t = dt.datetime.fromisoformat(v[0].replace("Z", "+00:00")) - OVERLAP
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp(con, key, started):
    con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, started))


def refresh_my_prs(con, full=False):
    started = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff = watermark(con, "my_prs_fetched", full)
    n = 0
    for org in CFG.github_orgs:
        for it in search(f"org:{org} is:pr author:{U}", cutoff):
            pr = it.get("pull_request") or {}
            con.execute("INSERT OR REPLACE INTO my_prs VALUES (?,?,?,?,?,?,?,?,?)", (
                repo_of(it), it["number"], it["title"], it["state"], it["created_at"], it.get("closed_at"),
                pr.get("merged_at"), it["updated_at"], it["html_url"]))
            n += 1
    stamp(con, "my_prs_fetched", started)
    con.commit()
    return n


def _one_reviewed(repo, num, timeline):
    reviews = gh_list(f"repos/{repo}/pulls/{num}/reviews")
    inline = gh_list(f"repos/{repo}/pulls/{num}/comments")
    convo = gh_list(f"repos/{repo}/issues/{num}/comments")
    rows = [("review", r["id"], r["state"], r.get("submitted_at")) for r in reviews if r["user"]["login"] == U]
    rows += [("inline", c["id"], None, c["created_at"]) for c in inline if c["user"]["login"] == U]
    rows += [("convo", c["id"], None, c["created_at"]) for c in convo if c["user"]["login"] == U]
    reqs = []
    if timeline:
        reqs = [e["created_at"] for e in gh_list(f"repos/{repo}/issues/{num}/timeline")
                if e.get("event") == "review_requested" and (e.get("requested_reviewer") or {}).get("login") == U]
    return repo, num, rows, reqs


def refresh_reviews(con, full=False):
    started = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff = watermark(con, "reviews_fetched", full)
    cands = {}
    for org in CFG.github_orgs:
        for qual in ("reviewed-by", "commenter"):
            for it in search(f"org:{org} is:pr {qual}:{U} -author:{U}", cutoff):
                cands[(repo_of(it), it["number"])] = it
    timeline = CFG.on("review_response_time")
    with cf.ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda k: _one_reviewed(k[0], k[1], timeline), cands))
    for (repo, num, rows, reqs) in results:
        it = cands[(repo, num)]
        con.execute("INSERT OR REPLACE INTO reviewed_prs VALUES (?,?,?,?,?,?,?,?)", (
            repo, num, it["title"], it["user"]["login"], it["state"],
            (it.get("pull_request") or {}).get("merged_at"), it["updated_at"], it["html_url"]))
        for kind, rid, state, ts in rows:
            con.execute("INSERT OR REPLACE INTO my_reviews VALUES (?,?,?,?,?,?)", (kind, rid, repo, num, state, ts))
        for ts in reqs:
            con.execute("INSERT OR IGNORE INTO review_requests VALUES (?,?,?)", (repo, num, ts))
    stamp(con, "reviews_fetched", started)
    con.commit()
    return len(cands)


def fresh(con, key):
    v = con.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return bool(v) and dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(v[0].replace("Z", "+00:00")) < FRESH


def refresh(full=False, prs=True, reviews=True):
    con = connect()
    if not full and (not prs or fresh(con, "my_prs_fetched")) and \
            (not reviews or not CFG.on("reviews") or fresh(con, "reviews_fetched")):
        return con, {"skipped": "refreshed less than 10 minutes ago"}
    who = subprocess.run(["gh", "api", "user", "-q", ".login"], capture_output=True, text=True).stdout.strip()
    if who != U:
        sys.exit(f"gh is '{who}', not {U} — run: gh auth switch --user {U}")
    out = {}
    if prs:
        out["my_prs_refreshed"] = refresh_my_prs(con, full)
    if reviews and CFG.on("reviews"):
        out["reviewed_prs_refreshed"] = refresh_reviews(con, full)
    return con, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["refresh", "stats"])
    ap.add_argument("--full", action="store_true")
    a = ap.parse_args()
    if a.cmd == "refresh":
        _, out = refresh(a.full)
        print(json.dumps(out))
    else:
        con = connect()
        print(json.dumps({
            "db": db_path(),
            "my_prs": con.execute("SELECT count(*) FROM my_prs").fetchone()[0],
            "reviewed_prs": con.execute("SELECT count(*) FROM reviewed_prs").fetchone()[0],
            "my_reviews": dict(con.execute("SELECT kind, count(*) FROM my_reviews GROUP BY kind").fetchall()),
            "fetched": dict(con.execute("SELECT k, v FROM meta").fetchall()),
        }, indent=2))


if __name__ == "__main__":
    main()
