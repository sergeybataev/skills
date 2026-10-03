# Done tracker — <Month YYYY>

Append-only log of completed work. **Newest at the bottom.** Nothing is edited or deleted — corrections are new, dated entries.

- Tickets read `KEY — title`; PRs `repo #number — title`.
- Timestamps are `YYYY-MM-DD` (or `YYYY-MM-DD HH:MM UTC` when a real timestamp exists). Never invent precision.
- **Origin tag on every entry:** `#initiative` (self-initiated — the strongest review evidence, and the work that leaves least trace), `#assigned` (ticket, commitment, request), `#incident` (incident, production issue, oncall). `grep '#initiative'` reconstructs a review cycle.

---
