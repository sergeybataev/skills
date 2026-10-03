"""Shared config loader for the worklog plugin.

Config lives outside the plugin so updates never overwrite it:
  $WORKLOG_CONFIG, else ~/.config/worklog/config.toml
Run /worklog:setup to create it.
"""
import datetime as dt
import os
import re
import sys
import tomllib

DEFAULT_PATH = "~/.config/worklog/config.toml"


def path():
    return os.path.expanduser(os.environ.get("WORKLOG_CONFIG", DEFAULT_PATH))


def _x(p):
    return os.path.expanduser(p) if isinstance(p, str) else p


class Config:
    def __init__(self, d):
        self.raw = d
        idn = d.get("identity", {})
        self.github_user = idn["github_user"]
        self.github_orgs = idn.get("github_orgs", [])
        self.commit_emails = idn.get("commit_emails", [])
        self.code_roots = [_x(p) for p in idn.get("code_roots", [])]
        self.repos = idn.get("repos", [])                 # "org/name"
        self.primary_checkouts = idn.get("primary_checkouts", [])
        self.base_branches = idn.get("base_branches", ["main", "master"])
        self.integration_branches = idn.get("integration_branches", [])
        # local scratch branches: counted, never flagged as unpushed work
        self.scratch_branches = idn.get("scratch_branches",
                                        ["backup/*", "tmp/*", "wip/*", "exp/*", "rebase-*", "throwaway/*"])

        j = d.get("jira", {})
        self.jira_enabled = j.get("enabled", False)
        self.jira = j
        self.ticket_prefixes = j.get("ticket_prefixes", [])

        s = d.get("schedule", {})
        self.timezone = s.get("timezone", "UTC")
        self.work_week = s.get("work_week", ["Mon", "Tue", "Wed", "Thu", "Fri"])

        p = d.get("paths", {})
        self.notes_root = _x(p["notes_root"])
        self.p = {k: os.path.join(self.notes_root, v) for k, v in p.items()
                  if k not in ("notes_root", "closeout_note", "perf_report")}
        self.p.setdefault("reports", os.path.join(self.notes_root, "reports"))
        # filename templates, relative to paths.reports: {date} {datetime} {slug} {month}
        self.templates = {
            "closeout": p.get("closeout_note", "closeouts/{date}-{slug}.md"),
            "perf": p.get("perf_report", "perf/{datetime}.md"),
        }

        w = d.get("wiki", {})
        self.wiki_enabled = w.get("enabled", False)
        self.wiki_hub = _x(w.get("hub", ""))
        self.wiki_topic = w.get("topic", "worklog")
        self.digests = (_x(w.get("digests", os.path.join(self.wiki_hub, ".sessions/digests")))
                        if self.wiki_enabled and self.wiki_hub else None)

        ss = d.get("sessions", {})
        self.transcripts_dir = _x(ss.get("transcripts_dir", "~/.claude/projects"))

        self.rules = d.get("rules", {}).get("items", [])
        self.features = d.get("features", {})

    # Every collected source and every output can be switched off in [features].
    # Defaults keep older configs working: jira/wiki-dependent features follow
    # jira.enabled / wiki.enabled; the slow review-response-time metric is opt-in.
    FEATURE_DEFAULTS = {
        "prs": True,                    # PRs you authored
        "reviews": True,                # reviews and comments you gave on others' PRs
        "review_response_time": False,  # time from review request to your review (1 extra API call per PR)
        "commits": True,
        "jira": None,                   # None → follow jira.enabled
        "transcripts": True,            # Claude Code transcripts (~/.claude/projects) — no llm-wiki needed
        "sessions": None,               # llm-wiki session digests → follow wiki.enabled
        "closeout": True,               # per-sync close-out note: llm-wiki if wiki on, else paths.reports
        "wiki_closeout": None,          # legacy name for closeout — honoured if closeout isn't set
        "perf_save": True,              # save each perf read under paths.reports
        "session_index": True,          # PR↔session resume index (transcripts, plus digests when wiki is on)
        "perf": True,                   # /worklog:perf
    }

    def on(self, name):
        if name == "closeout" and "closeout" not in self.features and "wiki_closeout" in self.features \
                and self.wiki_enabled:
            # legacy switch only means "no close-out" when there is a wiki to write to
            return bool(self.features["wiki_closeout"])
        if name in self.features:
            return bool(self.features[name])
        dflt = self.FEATURE_DEFAULTS.get(name, True)
        if dflt is None:
            return self.jira_enabled if name == "jira" else self.wiki_enabled
        return dflt

    def now(self):
        try:
            from zoneinfo import ZoneInfo
            return dt.datetime.now(ZoneInfo(self.timezone))
        except Exception:
            return dt.datetime.now()

    # ---- time windows: a bound is a date ("2026-10-02") or a datetime ("2026-10-02 19:40"),
    # read in schedule.timezone. Everything is compared as aware datetimes.
    def tz(self):
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(self.timezone)
        except Exception:
            return dt.timezone.utc

    def bound(self, s, end=False):
        s = s.strip().replace("T", " ")
        if len(s) == 10:
            d = dt.datetime.fromisoformat(s)
            d = d.replace(hour=23, minute=59, second=59) if end else d
        else:
            d = dt.datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=self.tz())

    def in_window(self, ts, since, until):
        """ts: GitHub/ISO timestamp string (UTC 'Z' or with offset)."""
        if not ts:
            return False
        d = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if not d.tzinfo:
            d = d.replace(tzinfo=dt.timezone.utc)
        return self.bound(since) <= d <= self.bound(until, end=True)

    def report_path(self, kind, slug=""):
        """Where a report goes. closeout → llm-wiki when the wiki is on, else paths.reports."""
        n = self.now()
        fields = {"date": n.strftime("%Y-%m-%d"), "datetime": n.strftime("%Y-%m-%d_%H%M"),
                  "month": n.strftime("%Y-%m"), "slug": re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-") or "sync"}
        if kind == "closeout" and self.wiki_enabled and self.wiki_hub:
            return os.path.join(self.wiki_hub, "topics", self.wiki_topic, "raw", "notes",
                                f"{fields['date']}-{fields['slug']}.md")
        return os.path.join(self.p["reports"], self.templates[kind].format(**fields))

    @property
    def ticket_re(self):
        if not self.ticket_prefixes:
            return re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
        return re.compile(r"\b(?:%s)-\d+\b" % "|".join(map(re.escape, self.ticket_prefixes)), re.I)

    def owns_cwd(self, cwd):
        """True if a session's cwd is inside one of my code roots."""
        cwd = os.path.realpath(_x(cwd))
        return any(cwd == r or cwd.startswith(r.rstrip("/") + "/") for r in map(os.path.realpath, self.code_roots))

    def git_repos(self):
        """Top-level git repos under the code roots (worktrees share the parent's objects)."""
        out = []
        for root in self.code_roots:
            if os.path.isdir(os.path.join(root, ".git")):
                out.append(root)
                continue
            if not os.path.isdir(root):
                continue
            for name in sorted(os.listdir(root)):
                p = os.path.join(root, name)
                if os.path.isdir(os.path.join(p, ".git")):
                    out.append(p)
        return out


def _cli():
    """wlconfig.py report-path closeout|perf [--slug words]  → prints the path (creates its folder)."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["report-path"])
    ap.add_argument("kind", choices=["closeout", "perf"])
    ap.add_argument("--slug", default="")
    a = ap.parse_args()
    c = load()
    if not c.on("closeout" if a.kind == "closeout" else "perf_save"):
        sys.exit(f"{a.kind} reports are off in [features]")
    out = c.report_path(a.kind, a.slug)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    print(out)


def load(required=True):
    p = path()
    if not os.path.exists(p):
        if required:
            sys.exit(f"no worklog config at {p} — run /worklog:setup first")
        return None
    with open(p, "rb") as f:
        return Config(tomllib.load(f))


if __name__ == "__main__":
    _cli()
