#!/usr/bin/env python3
"""Detect worklog config values from this machine. Read-only; prints JSON.

Everything here is a *candidate* — /worklog:setup shows it to the user to confirm.
Jira values can't be detected from a script; setup gets those through the Atlassian MCP.

  detect.py [--org ORG] [--user GH_LOGIN]
"""
import argparse, collections, glob, json, os, re, subprocess, time

H = os.path.expanduser("~")


def sh(cmd, cwd=None):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def tilde(p):
    return p.replace(H, "~", 1) if p.startswith(H) else p


def gh_accounts():
    out = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True).stdout + \
          subprocess.run(["gh", "auth", "status"], capture_output=True, text=True).stderr
    accts = re.findall(r"account (\S+)", out)
    active = sh(["gh", "api", "user", "-q", ".login"])
    return list(dict.fromkeys(accts)), active


def orgs_for(user):
    # orgs where this account authored PRs recently — more useful than membership lists
    data = sh(["gh", "search", "prs", "--author", user, "--limit", "1000",
               "--json", "repository"])
    c = collections.Counter(r["repository"]["nameWithOwner"] for r in json.loads(data or "[]"))
    orgs = collections.Counter()
    for full, n in c.items():
        orgs[full.split("/")[0]] += n
    return orgs.most_common(), c.most_common()


def code_roots(orgs):
    """Directories whose name is an org, holding git repos."""
    hits = []
    for org in orgs:
        for pat in (f"{H}/*/{org}", f"{H}/*/*/{org}", f"{H}/*/*/*/{org}", f"{H}/*/*/*/*/{org}"):
            for d in glob.glob(pat):
                if any(os.path.isdir(os.path.join(d, x, ".git")) for x in os.listdir(d)):
                    hits.append(d)
    return sorted(set(hits))


def emails(roots):
    seen = collections.Counter()
    g = sh(["git", "config", "--global", "user.email"])
    if g:
        seen[g] += 1
    for root in roots:
        for name in os.listdir(root):
            p = os.path.join(root, name)
            if os.path.isdir(os.path.join(p, ".git")):
                e = sh(["git", "config", "user.email"], cwd=p)
                if e:
                    seen[e] += 1
    return [e for e, _ in seen.most_common()]


def primary_checkouts(roots):
    """Repos that host .worktrees/ — sessions in their root carry checkout-artefact branches."""
    out = []
    for root in roots:
        for name in os.listdir(root):
            p = os.path.join(root, name)
            if os.path.isdir(os.path.join(p, ".worktrees")) or os.path.isdir(os.path.join(p, ".claude/worktrees")):
                out.append(name)
    return out


def base_branches(roots):
    c = collections.Counter()
    for root in roots:
        for name in os.listdir(root):
            p = os.path.join(root, name)
            if os.path.isdir(os.path.join(p, ".git")):
                h = sh(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=p)
                if h:
                    c[h.split("/", 1)[-1]] += 1
    return [b for b, _ in c.most_common()] or ["main"]


def llm_wiki():
    p = os.path.join(H, ".config/llm-wiki/config.json")
    try:
        d = json.load(open(p))
        hub = os.path.expanduser(d.get("hub_path") or d.get("resolved_path") or "")
        return hub if os.path.isdir(hub) else ""
    except Exception:
        return ""


def notes_candidates():
    """Existing worklog boards: TODO.md next to a *tracker* file, newest first."""
    found = []
    for base in (f"{H}/ObsidianVault", f"{H}/Documents", f"{H}/notes", f"{H}/Notes"):
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("archive", "node_modules")]
            if root.count(os.sep) - base.count(os.sep) > 6:
                dirs[:] = []
            if "TODO.md" in files and any("tracker" in f.lower() for f in files):
                found.append((os.path.getmtime(os.path.join(root, "TODO.md")), root))
    return [r for _, r in sorted(found, reverse=True)]


def timezone():
    tz = os.environ.get("TZ") or ""
    if not tz and os.path.islink("/etc/localtime"):
        tz = os.readlink("/etc/localtime").split("zoneinfo/")[-1]
    return tz or time.tzname[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user")
    ap.add_argument("--org", action="append")
    a = ap.parse_args()
    accts, active = gh_accounts()
    user = a.user or active
    orgs, repos = orgs_for(user) if user else ([], [])
    org_names = a.org or [o for o, _ in orgs[:3]]
    roots = code_roots(org_names)
    hub = llm_wiki()
    print(json.dumps({
        "gh_accounts": accts, "gh_active": active, "gh_user_used": user,
        "orgs_by_pr_count": orgs,
        "repos_by_pr_count": [(r, n) for r, n in repos if r.split("/")[0] in org_names],
        "code_roots": [tilde(r) for r in roots],
        "commit_emails": emails(roots),
        "primary_checkouts": primary_checkouts(roots),
        "base_branches": base_branches(roots),
        "timezone": timezone(),
        "llm_wiki_hub": tilde(hub),
        "llm_wiki_digests_exist": bool(hub) and os.path.isdir(os.path.join(hub, ".sessions/digests")),
        "notes_candidates": [tilde(r) for r in notes_candidates()[:5]],
    }, indent=2))


if __name__ == "__main__":
    main()
