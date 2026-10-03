---
name: sync
description: Sync a personal worklog from GitHub PRs, Jira, git commits and Claude/Codex session digests into a notes folder (open-items board, append-only done-tracker, weekly sprint deltas, llm-wiki close-out note, PR↔session resume index). Use whenever the user asks to update/sync/close out their worklog or TODOs, check their PRs and Jira tickets, close or promote sessions, roll over to a new sprint, log an oncall week, or asks what to do next week — even phrased casually like "check again", "update worklog", "we did a lot of work", "new sprint".
---

# Worklog sync

The worklog keeps someone's work visible at sprint reviews and performance cycles. Throughput is rarely the problem; **legibility and closure are** — unrequested reviewers, unfiled tickets, approved PRs left in draft, untracked self-initiated work. The sync surfaces exactly those gaps, with numbers, and records what got done before it's forgotten.

## Before anything: config

Scripts are in `../../scripts/` relative to this skill's base directory — call that `$S`. Load the config:
```bash
python3 -c "import sys; sys.path.insert(0,'$S'); import wlconfig, json; c=wlconfig.load(False); print(json.dumps(c.raw, indent=1, default=str) if c else 'NO CONFIG')"
```
**No config → run the `setup` skill first** (tell the user, then follow it), and come back.

Read the whole config: identity, Jira, schedule, paths, wiki — and **`rules.items`, which are the user's standing rules for this job. They override anything generic below.** Below, `<board>`, `<tracker>` etc. mean the configured paths under `paths.notes_root`.

| File | Job |
|---|---|
| `<board>` | The board: **only what is still open** |
| `<tracker>` | Append-only done-log, timestamped, origin-tagged |
| `<sprint_doc>` (`{Month}` → the month in the sprint's name, else its start month) | Week-by-week narrative deltas — one new `## 0` per sync |
| `<wiki.hub>/topics/<wiki.topic>/raw/notes/YYYY-MM-DD-<slug>.md` | Durable close-out note per sync (if wiki enabled) |
| `<sessions_index>` + `<sessions_db>` | PR/ticket/branch → resumable sessions |
| `<archive_dir>/YYYY-MM-*` | Previous sprint's board + tracker |

If the board has a **How to use** section, read it — it may hold conventions newer than this skill.

## Standing rules (on top of `rules.items`)

- **Never `git commit`** anything, in the notes folder or the repos.
- **No Jira or GitHub writes** — transitions, comments, PR state changes — unless the user explicitly asks for that specific action. Offer, don't do.
- Tickets in `jira.read_only_projects` are someone else's to transition: use them for context only, never list their status as the user's action. Never make a transition in `jira.forbidden_transitions`.
- **Tracker and wiki notes are append-only.** A wrong earlier claim gets a dated correction appended, never an edit.
- Phrase closure steps (request reviewers, file the ticket, update status) as plain mechanical checklist items — no moralising. For many people asking for review is the costly step; a checklist removes the decision.

## Step 1 — window

Start at the board's **`Last synced …` line** — the authoritative marker; every write-sync updates it. If the newest tracker or sprint-delta date is later, use the latest of the three and say so. A same-day earlier sync means the window overlaps: compare against what the tracker already records rather than re-logging. `since` is inclusive. Ends today (in `schedule.timezone`). State the window in one line. **Compute weekdays with code** (`python3 -c "import datetime;print(datetime.date(Y,M,D).strftime('%a'))"`) — never by hand, and `date -j` isn't portable.

## Step 2 — collect (in parallel)

**GitHub** — check the account first; a wrong account returns an *empty list, not an error*:
```bash
gh api user -q .login     # must equal identity.github_user; else gh auth switch --user <it>
# for each org in identity.github_orgs:
gh search prs --author <user> --owner <org> --merged --merged-at ">=<since>" --limit 200 --json repository,number,title,closedAt
gh search prs --author <user> --owner <org> --state closed --limit 200 --json repository,number,title,closedAt
gh search prs --author <user> --owner <org> --state open   --limit 200 --json repository,number,title,createdAt,isDraft
```
Org-wide, never a hand-picked repo list. `--state closed` includes merged; closed-unmerged = closed minus merged.

Every open PR: `gh pr view <n> -R <owner/repo> --json isDraft,reviewRequests,reviews,reviewDecision,mergeable,baseRefName`. Read carefully — shortcuts here have produced wrong advice:
- **Approval** = a human's *latest* review is APPROVED. A later CHANGES_REQUESTED overrides it; bot approvals (`github-actions`) and the user's own reviews don't count. A human approval with `reviewDecision: REVIEW_REQUIRED` means more approvals (or a code owner) are still needed — say so rather than calling it approved.
- **Stacked** — `baseRefName` not in `identity.base_branches` *or* `identity.integration_branches` means it targets another PR's branch and can't merge before it. Name the base.
- **"Zero reviewers"** = no pending `reviewRequests` *and* no human `reviews` (`reviewRequests` empties once people review).
- Findings that matter: approved-but-draft, mergeable with zero reviewers, reviewed-but-undecided, conflicting.

Merged PRs: read the body (`gh pr view <n> --json body`) for measured effects — latency, memory, minutes, parity counts. Those numbers make the tracker entry useful later.

**Jira** (if `jira.enabled`; Atlassian MCP, `cloudId = jira.cloud_id`):
- Open + recent: `(assignee = currentUser() AND (updated >= "<since>" OR statusCategory != Done)) OR (reporter = currentUser() AND created >= "<since>")`, plus keys referenced by in-window commits/PRs that aren't the user's (to see who owns them).
- Changed: `assignee = currentUser() AND updated >= "<since>"`; if empty, run a wider positive control (`<since − 14d>`) — empty can mean "nothing changed" or "query broken".
- Request `jira.sprint_field` and read sprint name, board, `endDate`, `state`, `completeDate` from it. Sprint dates move — read them, never assume. `endDate` is UTC: convert to `schedule.timezone` and state the weekday.
- `openSprints()` is board-agnostic; with several `jira.boards`, report each board's sprint separately. Watch for sprints that closed and left the user's tickets sprint-less.

**Commits**:
```bash
python3 $S/collect.py --since <since> --until <today> --no-github --no-sessions   # stdout only
```
It filters by committer date, so rebased commits land in the window (listed separately). Report **unique subjects by author date**; drop git-stash pseudo-commits (`index on …`, `untracked files on …`).

Unpushed work is a finding. For each repo with in-window commits:
```bash
git fetch --quiet origin
git for-each-ref --format='%(refname:short) %(committerdate:short)' refs/heads | awk '$2 >= "<since>"'
git rev-list --count <branch> --not --remotes      # commits that exist on no remote branch at all
```
Non-zero = unpushed work. (A squash-merged branch also shows commits here — check `gh pr list --head <branch> --state merged` before flagging it.)

**Sessions** (if `wiki.enabled`) — parse frontmatter of `<wiki.hub>/.sessions/digests/**/*.md`; keep only `cwd` inside an `identity.code_roots` path (other tools create many empty digests).
- In window = `last_seen_at` in window. But `tool_event_count` is a **lifetime** total — for the effort split count only sessions whose `started_at` is in the window; list resumed ones by name without counts.
- **Unpromoted** = `promoted_to` is `[]`/empty (a regex like `promoted_to:\s*\S` matches `[]` — don't). Also check digests from the **previous sync's window** only: a session still live after that sync gets its frontmatter rewritten by the capture hook, so its promotion reverts. Re-promote those to the previous close-out note (the one whose window contains their `last_seen_at`) and say how many. Older `[]` digests predate the promotion practice — leave them.
- **Branch attribution** (digest field `git_branch`) — sessions in a `identity.primary_checkouts` root carry whatever branch was checked out; that share is an artefact. Worktree sessions are usually reliable, but a deleted/detached worktree reports the primary's branch too. Trust a branch only when the worktree folder plausibly matches it; say which shares are artefacts.

**Things no source can see** — oncall/support rotations, meetings, reviews given. If the user mentions them or the window covers a rotation, ask for the thread or list rather than guessing.

## Step 3 — diff against the board

Walk every board item against what you collected. Stale claims are often the most valuable finding: a PR the board calls mergeable that now has changes requested, items listed open that merged, a sprint end date that moved. Report each as a correction.

## Step 4 — report or write?

- "check", "what changed", "summary", "what should I do" → **report only**, then offer to write.
- "update", "sync", "log", "close sessions", "ingest" → write (step 5) and report.

## Step 5 — write

**New sprint?** (sprint name differs from the board header) — copy board and tracker to `<archive_dir>/YYYY-MM-<name>`, fix relative links inside the archived copies for their new depth, start a fresh pair with the same header and conventions, and a new `<sprint_doc>`. Templates: `assets/`.

**Board** — scripts are the plugin's (`$S`); if the board's how-to points at older copies elsewhere, say so. Update header (sprints per board, end dates in local time, `Last synced <date> (<weekday>)`); remove finished items (they go to the tracker first); add new open items. Conventions:
- `KEY — title` for tickets, `repo #number — title` for PRs. No bare numbers.
- Top-level box = fully done (merged/closed); steps as nested sub-boxes. **Never two `- [ ]` on one line** — Obsidian and most renderers make only the first a checkbox.
- Sections ordered by whether the user must act.

**Tracker** — append `## <since> → <today>`. One bullet per completed thing: `- **YYYY-MM-DD** — <what> **merged**/**Done** … #tag`, with measured results. Tag every entry:
- `#initiative` — self-initiated (tag even if a ticket was filed afterwards). The strongest promotion evidence, and the work that leaves least trace.
- `#assigned` — ticket, sprint commitment or request.
- `#incident` — incident, production issue, oncall.

**Sprint doc** — every write-sync gets a delta, even a short one (a skipped delta breaks the next window). Insert `## 0. Week-N delta (<since> → <today>)` above the previous one and rename the previous to `## 0b` (shift `0b`→`0c`…). Narrative: what changed, why it matters, what didn't move. No standing sections.

**Wiki close-out** (if enabled) — `<hub>/topics/<topic>/raw/notes/<today>-<slug>.md` with frontmatter (`title`, `source: "MANUAL"`, `type: notes`, `ingested`, `tags`, `summary`): effort table, merged list, work with no record, structural gaps. Then add a row at the top of `raw/notes/_index.md` (set `Last updated`), prepend a Recent Changes bullet to `raw/_index.md` and the topic `_index.md`, set its `Sources:` count from an actual file count, and set `promoted_to: ["topics/<topic>/raw/notes/<file>"]` on every in-window digest whose list was empty.

**Session index** — `python3 $S/session_index.py` (~45 s).

Read-only safety: `collect.py` writes only with `--out`; `perf_metrics.py` never writes; `session_index.py` **does** write — don't run it for report-only requests.

## Step 6 — verify before claiming done

- Every relative link in files you touched resolves (`%20` → space).
- `grep -c '\[ \].*\[ \]' <board>` is 0.
- Promoted count matches the in-window count, re-checked *after* writing; say how many live sessions will revert.
- Numbers reported were recomputed, not carried over. If two computations disagree, find out why before reporting either.

## Step 7 — the reply

Lead with the window and the one or two things that matter most. Then:
1. **What shipped** — merged PRs grouped by stream, with measured effects.
2. **Work with no record** — unpushed branches, unticketed effort, epics not updated.
3. **Action list** — numbered, cheapest/highest-leverage first.
4. **Files written**, and any correction to an earlier claim — plainly, once.

"What do I do next week" → just the action list, grouped: tickets · merge or unblock · request reviewers · close stale · before the sprint review.

For a monthly or performance read, use the `perf` skill.
