---
name: setup
description: Create or update the worklog plugin's config (~/.config/worklog/config.toml) — GitHub identity and orgs, Jira site/boards/sprint field, notes and llm-wiki paths, timezone, and personal rules. Detects most values from the machine and asks only to confirm and fill gaps. Use when the user runs /worklog:setup or /worklog:init, asks to configure or reconfigure the worklog, changes jobs/orgs/boards/vault, or when another worklog skill finds no config.
---

# Worklog setup

Goal: a correct `~/.config/worklog/config.toml` (or `$WORKLOG_CONFIG`) with as few questions as possible. **Detect first, ask second** — the user should mostly be confirming, not typing.

Scripts are in `../../scripts/` relative to this skill's base directory. Call that `$S`.

## 0. Existing config?

`python3 -c "import sys; sys.path.insert(0,'$S'); import wlconfig; print(wlconfig.path())"` and check whether it exists. If it does, show a short summary of what's in it and ask what to change — don't redo everything. Edits to a few fields can be made directly in the TOML; a full rerun uses `write_config.py --force` (it keeps a backup).

## 1. Detect

```bash
python3 $S/detect.py            # ~15 s, read-only, prints JSON
```
It returns gh accounts and the active one, orgs and repos ranked by how many PRs the user authored there, code roots (dirs named after an org that hold git repos), commit emails, primary checkouts (repos hosting `.worktrees/`), default base branches, system timezone, the llm-wiki hub, and existing board folders.

If the active gh account isn't the work one, rerun with `--user <login>` — and remember a non-work active account makes every later sync return empty PR lists.

## 2. Jira (optional)

If the Atlassian MCP is available (load via ToolSearch: `getAccessibleAtlassianResources`, `atlassianUserInfo`, `searchJiraIssuesUsingJql`):
1. `getAccessibleAtlassianResources` → `cloud_id`, `site`.
2. `searchJiraIssuesUsingJql` with `assignee = currentUser() ORDER BY updated DESC`, `maxResults: 30`, `view: "evidence"`.
3. **Sprint field** — find the custom field whose values are lists of objects with `boardId`, `name`, `state`, `endDate`. Its id (e.g. `customfield_10020`-style id) is `jira.sprint_field`. Don't guess from the name alone: sites often have a second "Sprint"-ish field that holds something else.
4. **Boards** — distinct `boardId` among *active* sprints in those values; record each with an example sprint name so the sync can recognise the naming pattern.
5. **Ticket prefixes** — distinct project keys from the issue keys, plus any seen in recent commit subjects.

If there's no Atlassian MCP or the user doesn't use Jira, set `jira.enabled = false` — the sync works without it.

## 3. Ask — confirm and fill gaps

Use AskUserQuestion, at most 4 questions per call, recommended option first, detected values pre-filled in the option labels. Cover:

- **Branches**: confirm default base branches, and ask for long-lived **integration branches** PRs legitimately target (e.g. a team `develop`) — without them every PR into one looks stacked. Show the default **scratch branch** patterns (`backup/*`, `tmp/*`, `wip/*`, `exp/*`, `rebase-*`, `throwaway/*`) — local branches matching them are never reported as unpushed work — and ask if they use others.
- **Identity**: work gh account; orgs to include (multi-select from detected); commit emails to count (multi-select — personal addresses often appear in work repos).
- **Schedule**: timezone (the system zone may not be the work zone — ask explicitly) and work week (e.g. Sun–Thu vs Mon–Fri).
- **Notes**: use the detected board folder, or a new folder (create from `../sync/assets/` templates). Separate board/tracker filenames only if they already use different ones.
- **Reports folder** (`paths.reports`, relative to the notes folder, default `reports`): where close-out notes go when there's no wiki, and where each perf read is saved. Filenames are templates — `closeout_note` (default `closeouts/{date}-{slug}.md`) and `perf_report` (default `perf/{datetime}.md`); fields `{date}` `{datetime}` `{month}` `{slug}`. Only ask about the templates if the user wants a different layout.
- **Features** (multi-select, all on by default except the slow one): PRs authored, reviews given, review response time (slower — one extra API call per reviewed PR), commits, Jira, Claude Code transcripts, llm-wiki session digests, per-sync close-out note, session index, monthly perf read, saving each perf read. The session index needs only transcripts; llm-wiki is optional. Write the answers to `[features]`; anything not asked keeps its default.
- **Wiki**: use the detected llm-wiki hub for session capture and close-out notes, or not.
- **Jira policy** (if enabled): projects whose tickets someone else transitions (e.g. a service-desk project closed by support), and transitions never to make (e.g. `QA -> Done`).
- **Rules**: free text — anything the sync must always or never do in this job (branches that never get a PR, how to phrase reminders, who owns what). Offer to import relevant items from the user's memory/CLAUDE.md files if they exist, and show what you'd import.

## 4. Write

Build a JSON object with the TOML's shape and render it:
```bash
python3 $S/write_config.py --from <scratch>/answers.json --dry-run   # show the user
python3 $S/write_config.py --from <scratch>/answers.json [--force]
```
Shape (omit what doesn't apply):
```json
{
  "identity": {"github_user": "", "github_orgs": [], "commit_emails": [], "code_roots": ["~/src/org"],
               "repos": ["org/name"], "primary_checkouts": [], "base_branches": ["main"],
               "integration_branches": [], "scratch_branches": ["backup/*", "tmp/*", "wip/*", "exp/*", "rebase-*", "throwaway/*"]},
  "features": {"prs": true, "reviews": true, "review_response_time": false, "commits": true,
               "jira": true, "transcripts": true, "sessions": true, "closeout": true, "session_index": true, "perf": true,
               "perf_save": true},
  "jira": {"enabled": true, "site": "", "cloud_id": "", "sprint_field": "customfield_NNNNN",
           "ticket_prefixes": [], "read_only_projects": [], "forbidden_transitions": [],
           "boards": [{"id": 0, "name": "", "sprint_example": ""}]},
  "schedule": {"timezone": "Area/City", "work_week": ["Mon","Tue","Wed","Thu","Fri"]},
  "paths": {"notes_root": "~/...", "board": "TODOs/TODO.md", "tracker": "TODOs/TODO_tracker.md",
            "archive_dir": "TODOs/archive", "sprint_doc": "{Month} sprint.md",
            "sessions_index": "Sessions index.md", "sessions_db": "sessions.sqlite",
            "reports": "reports"},
  "wiki": {"enabled": true, "hub": "~/...", "topic": "worklog"},
  "rules": {"items": []}
}
```
`paths.*` other than `notes_root` are relative to `notes_root`.

## 5. Verify and hand off

- `python3 $S/collect.py --days 7 --no-github` runs without error and finds commits (positive control — zero commits usually means a wrong email or code root).
- If the notes folder is new, create the board and tracker from `../sync/assets/`.
- Tell the user where the config is, that they can edit it by hand, and that `/worklog:sync` is next.
