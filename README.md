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
- Optional: [llm-wiki](https://github.com/nvk/llm-wiki) — close-out notes go into the wiki instead of the reports folder, and sessions get promoted per sync. The session index works without it (it reads Claude Code's own transcripts); with it, digests add Codex sessions, sessions whose transcript was deleted, and the branch a session ended on.

### Config

Everything specific to you lives in `~/.config/worklog/config.toml` (or `$WORKLOG_CONFIG`), never in the plugin:

- `[features]` — switch each part on or off: `prs`, `reviews`, `review_response_time` (off by default — one extra API call per reviewed PR), `commits`, `jira`, `transcripts`, `sessions` (llm-wiki digests), `closeout`, `session_index`, `perf`, `perf_save`
- `[sessions]` — `transcripts_dir` (default `~/.claude/projects`)
- `[identity]` — GitHub user, orgs, commit emails, code roots, repos, base and integration branches
- `[jira]` — site, cloud id, sprint field, boards, ticket prefixes, projects someone else transitions, forbidden transitions
- `[schedule]` — timezone and work week
- `[paths]` — notes folder and the board / tracker / sprint-doc / session-index files inside it, plus `reports` (default `reports/`) with filename templates `closeout_note` (`closeouts/{date}-{slug}.md`) and `perf_report` (`perf/{datetime}.md`). Close-out notes go to llm-wiki when it's enabled, otherwise here; every perf read is saved here.
- `[wiki]` — llm-wiki hub and topic
- `[perf]` — `months`: how far back `/worklog:perf` looks. No default — setup asks (quarter, half-year, annual review, since you joined).
- `[rules]` — your standing rules in plain English ("never open a PR from branch X", "tickets in project Y are closed by support"). The skills treat them as overriding their defaults.

### Reviews you gave

```bash
python3 <plugin>/scripts/reviews.py --since 2026-09-01 [--response-time] [--json]
```
PRs you reviewed or commented on that someone else wrote, with your verdict, inline-comment count and (optionally) how long you took after being asked. Only your activity inside the window counts.

### Jump back into a session

Built from Claude Code's transcripts (`~/.claude/projects/*/<id>.jsonl`, parsed once and cached — later rebuilds take seconds), merged with llm-wiki digests if you use it. A session that moved between worktrees is linked to every branch it worked on, and the resume command uses the folder the session was started in — the only place `claude --resume` finds it.

```bash
python3 <plugin>/scripts/session_index.py --pr 1234        # resume commands, best evidence first
python3 <plugin>/scripts/session_index.py --ticket ABC-123
python3 <plugin>/scripts/session_index.py --branch feature/x       # falls back to sessions that mention it
```

### Scripts

All in `plugins/worklog/scripts/`, all read the config, all accept `--since`/`--until` as a date or `"YYYY-MM-DD HH:MM"`:

| Script | Does |
|---|---|
| `syncstate.py window \| mark \| week` | sync window from the exact time of the last sync; current work week |
| `prs.py` | merged / closed / open PRs with review findings (approved-but-draft, needs another approval, changes requested, stacked, conflicting, no reviewers) and unpushed branches |
| `reviews.py` | reviews and comments you gave on others' PRs |
| `collect.py` | commits by day, deduplicated |
| `sessions.py effort \| status \| promote \| verify` | effort inside the window from transcript timestamps; llm-wiki promotion |
| `session_index.py` | PR/ticket/branch → resumable sessions |
| `perf_metrics.py` | monthly delivery table |
| `closeout.py index` · `verify.py` | wiki index bookkeeping · dead links and double checkboxes |

### Safety

The skills never commit, never transition tickets, never comment on PRs or issues, and never change PR state unless you explicitly ask for that specific action. The tracker is append-only: corrections are new dated entries.
