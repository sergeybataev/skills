# skills

Claude Code plugins I use and share. MIT licensed.

```bash
claude plugin marketplace add sergeybataev/skills
claude plugin install worklog@sergeybataev-skills
```

## worklog

A personal worklog built from what you actually did — GitHub PRs you wrote, reviews you gave, Jira tickets, git commits and Claude/Codex sessions — kept in a notes folder (works well with Obsidian). It exists to keep your work visible at sprint reviews and performance cycles, and to surface the small closure gaps that hide it: PRs with no reviewer requested, approved PRs left in draft, untracked work, unpushed branches.

| Command | What it does |
|---|---|
| `/worklog:setup` | Detects your GitHub account, orgs, repos, code folders, Jira site/boards/sprint field, notes folder and timezone; asks you only to confirm and fill gaps; writes `~/.config/worklog/config.toml`. |
| `/worklog:sync` | Reports what changed since the last sync — or updates the board (open items), an append-only done-tracker tagged `#initiative`/`#assigned`/`#incident`, a weekly sprint delta, an optional [llm-wiki](https://github.com/nvk/llm-wiki) close-out note, and a PR↔session index. Runs setup first if there's no config. |
| `/worklog:perf` | Monthly delivery read: merges, time-to-merge, commits per merged PR, ticket coverage, regressions, sprint-goal fit. |

### Requirements

- `gh` (GitHub CLI), authenticated as the account whose PRs you want tracked
- Python 3.11+ (stdlib only)
- Optional: the Atlassian MCP server for Jira
- Optional: [llm-wiki](https://github.com/nvk/llm-wiki) — only for session tracking, the wiki close-out note and the PR↔session index. Without it, set `[wiki] enabled = false`; PRs, reviews, commits, Jira and the perf read all work on their own.

### Config

Everything specific to you lives in `~/.config/worklog/config.toml` (or `$WORKLOG_CONFIG`), never in the plugin:

- `[features]` — switch each part on or off: `prs`, `reviews`, `review_response_time` (off by default — one extra API call per reviewed PR), `commits`, `jira`, `sessions`, `wiki_closeout`, `session_index`, `perf`
- `[identity]` — GitHub user, orgs, commit emails, code roots, repos, base and integration branches
- `[jira]` — site, cloud id, sprint field, boards, ticket prefixes, projects someone else transitions, forbidden transitions
- `[schedule]` — timezone and work week
- `[paths]` — notes folder and the board / tracker / sprint-doc / session-index files inside it
- `[wiki]` — llm-wiki hub and topic
- `[rules]` — your standing rules in plain English ("never open a PR from branch X", "tickets in project Y are closed by support"). The skills treat them as overriding their defaults.

### Reviews you gave

```bash
python3 <plugin>/scripts/reviews.py --since 2026-09-01 [--response-time] [--json]
```
PRs you reviewed or commented on that someone else wrote, with your verdict, inline-comment count and (optionally) how long you took after being asked. Only your activity inside the window counts.

### Jump back into a session

```bash
python3 <plugin>/scripts/session_index.py --pr 1234        # resume commands, best evidence first
python3 <plugin>/scripts/session_index.py --ticket ABC-123
python3 <plugin>/scripts/session_index.py --branch feature/x
```

### Safety

The skills never commit, never transition tickets, never comment on PRs or issues, and never change PR state unless you explicitly ask for that specific action. The tracker is append-only: corrections are new dated entries.
