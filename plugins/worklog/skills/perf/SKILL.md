---
name: perf
description: Data-led monthly performance read from the worklog — merged PRs and time-to-merge, commits, commits per merged PR (PR granularity), ticket coverage, regressions, sprint-goal fit, origin-tag counts — plus a defects brief on request. Use when the user asks how their month/quarter went, to analyse their performance or delivery, to prepare review or promotion evidence, or for a brief on defects found.
---

# Performance read

Scripts are in `../../scripts/` relative to this skill's base directory (`$S`). Respects `[features]` — if `perf` is off, say so and stop. Needs the worklog config — if `python3 $S/perf_metrics.py --months 1` says there's no config, run the `setup` skill first.

```bash
python3 $S/perf_metrics.py          # table over [perf] months (adds reviews columns when features.reviews is on)
python3 $S/perf_metrics.py --json   # for further analysis; --months N overrides the config for one run
```

**If it says `perf.months is not set`** (setup was skipped or the user answered later), ask the user how many months the read should cover — AskUserQuestion, options like "3 months (this quarter)", "6 months (half-year review)", "12 months (annual review)", "since I joined" (then ask the date and compute the months). Don't pick one for them: the right span depends on what the read is for. Save the answer so it isn't asked again:
```bash
python3 $S/write_config.py --set perf.months=<N>
```

**Save the read** (`perf_save`, on by default): once you've written the final answer, save the same markdown — table, narrative, verdict — to the path from `python3 $S/wlconfig.py report-path perf` (`perf/{datetime}.md` under `paths.reports` by default, so repeated runs never overwrite each other). Tell the user where it went. Read previous files in that folder first if the user asks how things changed.

Then read `reference.md` (next to this file) for which metrics have proved meaningful, the other sources to add (Jira, sessions, sprint goal, tracker tags), and how to structure the answer. Apply the config's `rules.items` to tone and framing.
