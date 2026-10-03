---
name: perf
description: Data-led monthly performance read from the worklog — merged PRs and time-to-merge, commits, commits per merged PR (PR granularity), ticket coverage, regressions, sprint-goal fit, origin-tag counts — plus a defects brief on request. Use when the user asks how their month/quarter went, to analyse their performance or delivery, to prepare review or promotion evidence, or for a brief on defects found.
---

# Performance read

Scripts are in `../../scripts/` relative to this skill's base directory (`$S`). Needs the worklog config — if `python3 $S/perf_metrics.py --months 1` says there's no config, run the `setup` skill first.

```bash
python3 $S/perf_metrics.py --months 6          # table
python3 $S/perf_metrics.py --months 6 --json   # for further analysis
```

Then read `reference.md` (next to this file) for which metrics have proved meaningful, the other sources to add (Jira, sessions, sprint goal, tracker tags), and how to structure the answer. Apply the config's `rules.items` to tone and framing.
