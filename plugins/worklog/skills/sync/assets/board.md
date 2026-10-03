# TODOs

**Sprint: `<name>`** · ends <weekday> <date> <time> (local)
Last synced: <date> (<weekday>)

**Conventions**

- Every ticket reads `KEY — title`; every PR reads `repo #number — title`. No bare numbers.
- The top-level box means **fully done** — merged / closed. Steps are nested sub-boxes, so "my part is done, waiting on review" reads as sub-boxes checked with the parent open. Never two boxes on one line.
- Sections are ordered by whether *you* need to act.
- **This file holds only what is left.** Finished work goes to the tracker (timestamp + `#initiative` / `#assigned` / `#incident`) and is then removed here.

## Closure checklist — before starting anything new

- [ ] Every PR opened since last time has reviewers requested — drafts included.
- [ ] Self-initiated work has a ticket, even a one-liner.
- [ ] Ticket statuses match reality.
- [ ] Finished items moved to the tracker.

## Action needed from me

## In flight

## This week

## Waiting on others

## Backlog

## Blocked

---

## How to use

- Run `/worklog:sync` to update, `/worklog:sync check` (or just ask "what changed?") for a report only.
- `/worklog:perf` for the monthly read; `/worklog:setup` to change the config.
- Jump back into the session behind a PR: `python3 <plugin>/scripts/session_index.py --pr <n>`.
