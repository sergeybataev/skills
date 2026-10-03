# Monthly performance read

The aim is an honest, data-led read he can use at a review — including the parts that don't flatter. A useful read names the mechanism behind a number, not just the number.

## Collect

```bash
python3 $S/perf_metrics.py            # over [perf] months: merges, TTM, commits, ratio, ticket coverage, reviews
python3 $S/perf_metrics.py --json     # same, machine-readable
```

Add from the other sources:
- **Jira**: tickets resolved and created in the month; overdue tickets (due date vs today); tickets that bounced back (QA → In Progress).
- **Sessions**: tool events by branch for the month (worktree branches only are reliable — see the branch-attribution note in the sync skill).
- **Sprint goal**: read it from the configured `jira.sprint_field` and say plainly how much of the month's work maps onto it.
- **The tracker**: count `#initiative` / `#assigned` / `#incident` for the month.

Commit counts come from branches that still exist locally (`git log --all`): deleting or rewriting local branches removes their unmerged commits from earlier months, so a month's count can shrink later. Merged-PR counts come from GitHub and don't move.

## Metrics that have proved meaningful

| Metric | Why it matters |
|---|---|
| Merged PRs / month, median + max time-to-merge | Throughput and the stall tail. TTM is usually **bimodal**: most PRs merge in ~1 day; a specific class sits 18–90 days. Find what that class shares (usually: no reviewer requested, cross-team reviewer, draft). |
| Commits per merged PR | PR granularity. Rising sharply (e.g. 4 → 13) usually means work concentrated into a few large PRs; re-cutting them into small ones typically restores throughput within a week. |
| Ticket coverage (% of commits citing a ticket) | Legibility. Low coverage (e.g. 13%) means most work has no organisational trace. |
| Effort vs output | Commits/session effort up while merges down = work concentrated into unmerged streams, not idleness. Say which. |
| Reviews given / month, changes-requested share, inline comments | Reviewing is output that never shows in the user's own PR list. All-approve with no inline comments over many months can read as rubber-stamping; a steady share of substantive reviews is strong evidence of technical leadership. |
| Review response time (opt-in) | Median hours from "review requested" to the user's first review. Long tails here are the mirror image of the user's own PRs stalling for reviewers. |
| Customer-visible regressions + detection gap | The detection gap (e.g. 14 days) is usually the more useful lesson than the defect. |

## Structure of the answer

1. **Table** — the month against the previous 3–5.
2. **The headline in one sentence** — what moved and the mechanism.
3. **What went well** — the strongest one or two results, with their measured effect.
4. **What went badly** — regressions, stalls, with cause.
5. **Structural numbers** — ticket coverage, overdue, reviewer-less PRs, unmeasured findings, sprint-goal fit.
6. **Verdict + the single highest-leverage correction.**

If asked for a defects brief: list each defect with its impact, how it was found, the known fix, and whether it has a ticket — lead with the one that is live in production.

Don't soften a bad month or pad a good one. It may be used as promotion evidence; an inflated read is worse than useless there. Equally, attribute unplanned work (oncall, escalations) correctly — it's real output that a sprint-goal view hides.
