#!/usr/bin/env python3
"""Write the worklog config from a JSON document on stdin (or --from file).

Writing TOML by hand invites quoting mistakes; setup builds a JSON object with the
same shape as the TOML and this script renders it. Refuses to overwrite an existing
config unless --force, and keeps a timestamped backup when it does.

  write_config.py --from answers.json [--force] [--dry-run]
"""
import argparse, datetime as dt, json, os, shutil, sys, tomllib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wlconfig  # noqa: E402

ORDER = ["identity", "jira", "schedule", "paths", "wiki", "rules"]
REQUIRED = {"identity": ["github_user", "github_orgs", "commit_emails", "code_roots"],
            "paths": ["notes_root", "board", "tracker"]}


def val(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)  # JSON string escaping is valid TOML basic-string
    if isinstance(v, list):
        if v and all(isinstance(x, dict) for x in v):
            return None  # array of tables, rendered separately
        items = [val(x) or "" for x in v]
        if any(len(x) > 40 for x in items):
            return "[\n" + "".join(f"  {x},\n" for x in items) + "]"
        return "[" + ", ".join(items) + "]"
    raise TypeError(f"unsupported value {v!r}")


def render(d):
    out = ["# worklog plugin config — edit freely, or rerun /worklog:setup", ""]
    for sect in ORDER + [k for k in d if k not in ORDER]:
        if sect not in d:
            continue
        body, tables = [], []
        for k, v in d[sect].items():
            r = val(v)
            if r is None:
                tables.append((k, v))
            else:
                body.append(f"{k} = {r}")
        out += [f"[{sect}]"] + body + [""]
        for k, rows in tables:
            for row in rows:
                out.append(f"[[{sect}.{k}]]")
                out += [f"{kk} = {val(vv)}" for kk, vv in row.items()]
                out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    d = json.load(open(a.src) if a.src else sys.stdin)
    missing = [f"{s}.{k}" for s, ks in REQUIRED.items() for k in ks if not d.get(s, {}).get(k)]
    if missing:
        sys.exit("missing required: " + ", ".join(missing))
    text = render(d)
    tomllib.loads(text)  # round-trip check before touching disk
    wlconfig.Config(tomllib.loads(text))  # and that the loader accepts it
    if a.dry_run:
        print(text)
        return
    p = wlconfig.path()
    if os.path.exists(p):
        if not a.force:
            sys.exit(f"{p} exists — rerun with --force to replace it (a backup is kept)")
        shutil.copy2(p, f"{p}.bak.{dt.datetime.now():%Y%m%d-%H%M%S}")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)
    print(f"wrote {p}")


if __name__ == "__main__":
    main()
