#!/usr/bin/env python3
"""Sync window and work-week bookkeeping.

  syncstate.py window          # {"since", "until", "source"} — since = exact time of the last sync
  syncstate.py mark            # record "synced now"; prints the board's "Last synced" text
  syncstate.py week [--date D] # current work week: start, end, label for the sprint-doc delta

State lives in <notes_root>/<paths.state> (default .worklog/state.json). Without it, the
window falls back to the board's "Last synced …" line (date or "date HH:MM"), then to 7 days.
"""
import argparse, datetime as dt, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wlconfig  # noqa: E402

CFG = wlconfig.load()
STATE = CFG.p.get("state", os.path.join(CFG.notes_root, ".worklog", "state.json"))
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
LAST_SYNCED = re.compile(r"Last synced[^:]*:\s*(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?")


def read_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def window():
    now = CFG.now().replace(second=0, microsecond=0)
    st = read_state()
    if st.get("last_synced"):
        return {"since": st["last_synced"], "until": now.strftime("%Y-%m-%d %H:%M"), "source": "state file"}
    try:
        m = LAST_SYNCED.search(open(CFG.p["board"]).read())
    except OSError:
        m = None
    if m:
        since = m.group(1) + (f" {m.group(2)}" if m.group(2) else "")
        return {"since": since, "until": now.strftime("%Y-%m-%d %H:%M"),
                "source": "board 'Last synced' line" + ("" if m.group(2) else " (date only — whole day re-checked)")}
    since = (now - dt.timedelta(days=7)).strftime("%Y-%m-%d")
    return {"since": since, "until": now.strftime("%Y-%m-%d %H:%M"), "source": "default: last 7 days"}


def mark():
    now = CFG.now().replace(second=0, microsecond=0)
    st = read_state()
    st["previous_sync"] = st.get("last_synced")
    st["last_synced"] = now.strftime("%Y-%m-%d %H:%M")
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w"), indent=2)
    return f"Last synced: {st['last_synced']} ({now.strftime('%a')})"


def week(date=None):
    d = dt.date.fromisoformat(date) if date else CFG.now().date()
    first = DAYS.index(CFG.work_week[0])
    start = d - dt.timedelta(days=(d.weekday() - first) % 7)
    end = start + dt.timedelta(days=len(CFG.work_week) - 1)
    return {"start": start.isoformat(), "end": end.isoformat(),
            "label": f"Week of {start.isoformat()} ({start.strftime('%a')}–{end.strftime('%a')})"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["window", "mark", "week"])
    ap.add_argument("--date")
    a = ap.parse_args()
    if a.cmd == "window":
        print(json.dumps(window()))
    elif a.cmd == "mark":
        print(mark())
    else:
        print(json.dumps(week(a.date)))


if __name__ == "__main__":
    main()
