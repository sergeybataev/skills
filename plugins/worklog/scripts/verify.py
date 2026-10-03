#!/usr/bin/env python3
"""Post-write checks for a sync. Exit code 1 if anything fails.

  verify.py [extra files...]

Checks the board, tracker, current sprint doc (newest matching paths.sprint_doc) and any
extra files given (e.g. the close-out note):
  - every relative markdown link resolves (%20 → space)
  - no line on the board carries two checkboxes (renderers make only the first one real)
"""
import glob, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wlconfig  # noqa: E402

CFG = wlconfig.load()
LINK = re.compile(r"\]\((?!https?:|mailto:)([^)#\s]+)(?:#[^)]*)?\)")


def sprint_doc():
    pat = CFG.raw.get("paths", {}).get("sprint_doc")
    if not pat:
        return None
    files = glob.glob(os.path.join(CFG.notes_root, pat.replace("{Month}", "*")))
    return max(files, key=os.path.getmtime) if files else None


def main():
    files = [CFG.p.get("board"), CFG.p.get("tracker"), sprint_doc(), *sys.argv[1:]]
    files = [f for f in files if f and os.path.exists(f)]
    bad = 0
    for f in files:
        d = os.path.dirname(f)
        for lineno, line in enumerate(open(f, errors="ignore"), 1):
            for link in LINK.findall(line):
                target = os.path.normpath(os.path.join(d, link.replace("%20", " ")))
                if not os.path.exists(target):
                    print(f"dead link  {os.path.relpath(f, CFG.notes_root)}:{lineno} → {link}")
                    bad += 1
    board = CFG.p.get("board")
    if board and os.path.exists(board):
        for lineno, line in enumerate(open(board), 1):
            if len(re.findall(r"- \[[ xX]\]", line)) > 1:
                print(f"two checkboxes  board:{lineno}")
                bad += 1
    print(f"checked {len(files)} file(s): " + ("ok" if not bad else f"{bad} problem(s)"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
