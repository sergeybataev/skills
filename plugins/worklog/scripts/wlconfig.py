"""Shared config loader for the worklog plugin.

Config lives outside the plugin so updates never overwrite it:
  $WORKLOG_CONFIG, else ~/.config/worklog/config.toml
Run /worklog:setup to create it.
"""
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

        j = d.get("jira", {})
        self.jira_enabled = j.get("enabled", False)
        self.jira = j
        self.ticket_prefixes = j.get("ticket_prefixes", [])

        s = d.get("schedule", {})
        self.timezone = s.get("timezone", "UTC")
        self.work_week = s.get("work_week", ["Mon", "Tue", "Wed", "Thu", "Fri"])

        p = d.get("paths", {})
        self.notes_root = _x(p["notes_root"])
        self.p = {k: os.path.join(self.notes_root, v) for k, v in p.items() if k != "notes_root"}

        w = d.get("wiki", {})
        self.wiki_enabled = w.get("enabled", False)
        self.wiki_hub = _x(w.get("hub", ""))
        self.wiki_topic = w.get("topic", "worklog")
        self.digests = _x(w.get("digests", os.path.join(self.wiki_hub, ".sessions/digests")))

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
        "sessions": None,               # session digests → follow wiki.enabled
        "wiki_closeout": None,          # llm-wiki close-out note → follow wiki.enabled
        "session_index": None,          # PR↔session resume index → follow wiki.enabled
        "perf": True,                   # /worklog:perf
    }

    def on(self, name):
        if name in self.features:
            return bool(self.features[name])
        dflt = self.FEATURE_DEFAULTS.get(name, True)
        if dflt is None:
            return self.jira_enabled if name == "jira" else self.wiki_enabled
        return dflt

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


def load(required=True):
    p = path()
    if not os.path.exists(p):
        if required:
            sys.exit(f"no worklog config at {p} — run /worklog:setup first")
        return None
    with open(p, "rb") as f:
        return Config(tomllib.load(f))
