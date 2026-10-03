---
name: sync
description: Sync a personal worklog from GitHub PRs, reviews, Jira, git commits and Claude/Codex sessions into a notes folder (open-items board, append-only done-tracker, one sprint-doc entry per work week, close-out note, PR↔session resume index). Use whenever the user asks to update/sync/close out their worklog or TODOs, check their PRs and Jira tickets, close or promote sessions, roll over to a new sprint, log an oncall week, or asks what to do next week — even phrased casually like "check again", "update worklog", "we did a lot of work", "new sprint".
---

# Worklog sync

The worklog keeps someone's work visible at sprint reviews and performance cycles. Throughput is rarely the problem; **legibility and closure are** — unrequested reviewers, unfiled tickets, approved PRs left in draft, untracked work, unpushed branches. The sync surfaces those gaps with numbers and records what got done before it's forgotten.

The scripts do the collecting and checking; your job is judgement — what matters, what's stale, what to tell the user. Don't re-implement what a script already does: if a script's output looks wrong, say so and fix the script rather than working around it.

## Setup

Scripts live in `../../scripts/` relative to this skill's base directory — call that `$S`. Every script reads `~/.config/worklog/config.toml`; if it's missing they print "run /worklog:setup first" — then run the `setup` skill and come back.

Read the config once (`python3 -c "import sys; sys.path.insert(0,'$S'); import wlconfig, json; print(json.dumps(wlconfig.load().raw, indent=1, default=str))"`). Two parts steer everything:
- **`rules.items`** — the user's standing rules for this job. They override anything generic below.
- **`[features]`** — `prs`, `reviews`, `review_response_time`, `commits`, `jira`, `transcripts`, `sessions`, `closeout`, `session_index`, `perf`, `perf_save`. Skip each step whose feature is off, and **say once in the reply what was skipped** — a silently missing source reads as "nothing happened".

If the board has a **How to use** section, read it — it may hold conventions newer than this skill.

## Standing rules (on top of `rules.items`)

- **Never `git commit`** anything, in the notes folder or the repos.
- **No Jira or GitHub writes** — transitions, comments, PR state — unless the user asks for that specific action. Offer, don't do.
- Tickets in `jira.read_only_projects` are someone else's to transition: context only, never the user's action item. Never make a transition in `jira.forbidden_transitions`.
- **Tracker and close-out notes are append-only.** A wrong earlier claim gets a dated correction appended, never an edit.
- Phrase closure steps (request reviewers, file the ticket, update status) as plain checklist items — no moralising. Asking for review is the costly step for many people; a checklist removes the decision.

## 1. Window

```bash
python3 $S/syncstate.py window      # {"since": "2026-10-03 17:55", "until": "...", "source": "..."}
# first sync with [sprint] source = "jira": it asks for the active sprint's start —
#   python3 $S/syncstate.py window --sprint-start <startDate of the active sprint, from jira.sprint_field>
python3 $S/syncstate.py week        # current work week: {"start", "end", "label"}
```
`since` is the exact time the last sync finished, so nothing is re-checked. A sync on a day outside `schedule.work_week` (e.g. a weekend) belongs to the work week that just ended — `week` already returns that one. Use `since`/`until` verbatim as `--since`/`--until` for every script below — they accept dates or "date HH:MM" in the configured timezone. State the window in one line; if `source` says "date only", mention that the whole first day is re-checked and compare against the tracker before logging anything.

## 2. Collect — run in parallel

```bash
python3 $S/prs.py      --since "$SINCE" --until "$UNTIL"        # merged, closed-unmerged, open PRs with findings, unpushed branches
python3 $S/reviews.py  --since "$SINCE" --until "$UNTIL" [--response-time]
python3 $S/collect.py  --since "$SINCE" --until "$UNTIL" --no-github --no-sessions --no-reviews   # commits by day
python3 $S/sessions.py effort --since "$SINCE" --until "$UNTIL" # tool calls inside the window, by branch
python3 $S/sessions.py status --since "$SINCE" --until "$UNTIL" # llm-wiki digests: in window / unpromoted
```

What the scripts already handle — don't redo it:
- **prs.py** — PRs from scratch branches or titled "never/do not merge" are tagged `scratch` and never flagged for reviewers. Unpushed also catches a branch whose commits reached the remote only through *another* branch (no remote branch of its own). Org-wide search filtered client-side (GitHub's date qualifiers lag), each human's *latest* review (bots and the user ignored), findings per open PR: `approved-but-draft`, `approved-needs-more` (approved but `REVIEW_REQUIRED`), `changes-requested`, `no-reviewers`, `stacked-on:<base>`, `conflicting`, `reviewed-undecided`. Unpushed = branches touched in 30 days with commits on no remote and no merged PR; `identity.scratch_branches` are counted, not flagged.
- **sessions.py effort** — from per-message transcript timestamps (minute resolution), so resumed sessions count only what they did in the window; branches worked mostly from a primary checkout are marked as possible artefacts.
- **collect.py** — unique commit subjects by author time, stash pseudo-commits dropped, rebased-in commits listed separately.

What still needs you:
- **Measured effects of merged PRs** — read their bodies (`gh pr view <n> -R <repo> --json body`) for latency, memory, minutes, parity counts. That's what makes a tracker entry useful later.
- **Jira** (`jira`; Atlassian MCP, `cloudId = jira.cloud_id`):
  - JQL takes the time too — use `"<since>"` as `"yyyy-MM-dd HH:mm"`.
  - Open + recent: `(assignee = currentUser() AND (updated >= "<since>" OR statusCategory != Done)) OR (reporter = currentUser() AND created >= "<since>")`, plus keys referenced by in-window commits/PRs that aren't the user's.
  - Changed: `assignee = currentUser() AND updated >= "<since>"`; if empty, run a wider positive control — empty can mean "nothing changed" or "query broken".
  - Request `jira.sprint_field`; read sprint name, board, `endDate` (UTC — convert to `schedule.timezone` and give the weekday), `state`, `completeDate`. With several `jira.boards`, report each board's sprint. Watch for closed sprints that left tickets sprint-less.
- **Things no source can see** — oncall rotations, meetings, pairing, reviews outside GitHub. If the window covers one, ask for the thread rather than guessing.

## 3. Diff against the board

Walk every board item against what you collected. Stale claims are often the most valuable finding — a PR the board calls mergeable that now has changes requested, items listed open that merged, a moved sprint end date, an unpushed-branch count that grew. Report each as a correction.

Then the other direction: every **open Jira ticket assigned to the user** and every **open PR** should appear somewhere on the board. List the ones that don't — they're drifting out of sight.

## 4. Report or write?

- "check", "what changed", "summary", "what should I do" → **report only** (no writes; `session_index.py`, `sessions.py promote` and `syncstate.py mark` all write), then offer to write.
- "update", "sync", "log", "close sessions", "ingest", or a bare `/worklog:sync` → write (step 5) and report.

## 5. Write

**New sprint?** (sprint name differs from the board header) — copy board and tracker to `<archive_dir>/YYYY-MM-<name>`, fix relative links in the copies for their new depth, start a fresh pair from `assets/`, and a new sprint doc.

**Board** — update the header (sprints per board, end dates in local time with weekday, and the `Last synced` text printed by `syncstate.py mark` in step 6); move finished items to the tracker and remove them; add new open items. Conventions:
- `KEY — title` for tickets, `repo #number — title` for PRs. No bare numbers.
- Top-level box = fully done; steps as nested sub-boxes. Never two `- [ ]` on one line.
- Sections ordered by whether the user must act.

**Tracker** — append `## <since> → <until>`. One bullet per completed thing, `- **YYYY-MM-DD** — <what> **merged**/**Done** … #tag`, with measured results. Tags:
- `#initiative` — self-initiated (even if ticketed afterwards). The strongest promotion evidence and the work that leaves least trace.
- `#assigned` — ticket, sprint commitment or request.
- `#incident` — incident, production issue, oncall.
- `#review` — **one summary line per sync** for reviews given, plus separate lines only for reviews that mattered (changes requested, a bug caught, a long thread).

**Sprint doc — one entry per work week, not per sync.** Take `label` from `syncstate.py week`:
- If the top `## 0` heading already has that label, **extend that entry** — add what's new and update its date range. Syncing daily must not produce a pile of one-day "week" entries.
- Otherwise insert `## 0. <label> (<since> → <until>)` above it and shift the old ones down (`0`→`0b`, `0b`→`0c`…).
Narrative: what changed, why it matters, what didn't move. No standing sections.

**Close-out note** (`closeout`) — one per sync.
```bash
NOTE=$(python3 $S/wlconfig.py report-path closeout --slug "<3-6 words>")   # llm-wiki when on, else paths.reports
# write the note: frontmatter (title, source: "MANUAL", type: notes, ingested, tags, summary),
# effort table from sessions.py, merged list, work with no record, corrections
python3 $S/closeout.py index --note "$NOTE" --summary "<one line>" --tags "<tags>" --recent "<changelog bullet>"
python3 $S/sessions.py promote --since "$SINCE" --until "$UNTIL" --note "topics/<topic>/raw/notes/<file>"   # wiki only
```
`closeout.py` is a no-op outside the wiki. Use the previous sync's `since` too when promoting if `status` showed reverted sessions from it.

**Session index** (`session_index`) — `python3 $S/session_index.py`. Transcripts are cached, so it takes seconds — except the first run after a plugin update that changes the parser, which re-reads everything (~1–2 min for a few GB). `sessions.py effort` shares the cache.

## 6. Verify, then mark

```bash
python3 $S/verify.py "$NOTE"                                    # dead links, double checkboxes — must exit 0
python3 $S/sessions.py verify --since "$SINCE" --until "$UNTIL" # sessions still live (incl. this one) will revert; say how many
python3 $S/syncstate.py mark                                    # records the sync time; put its output on the board's "Last synced" line
```
Numbers in the reply must come from this run's script output, not from memory of an earlier run. If two computations disagree, find out why before reporting either.

## 7. Reply

Lead with the window and the one or two things that matter most. Then:
1. **What shipped** — merged PRs grouped by stream, with measured effects.
2. **Reviews given** — count, verdict mix, any that mattered.
3. **Work with no record** — unpushed branches, unticketed effort, epics not updated.
4. **Action list** — numbered, cheapest/highest-leverage first.
5. **Files written**, skipped features, and any correction to an earlier claim — plainly, once.

"What do I do next week" → just the action list, grouped: tickets · merge or unblock · request reviewers · close stale · before the sprint review.

For a monthly or performance read, use the `perf` skill.
