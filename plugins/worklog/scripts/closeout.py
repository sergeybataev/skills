#!/usr/bin/env python3
"""Register a close-out note in the llm-wiki indexes (no-op outside the wiki).

  closeout.py index --note <path> --summary "<one line>" --tags "a, b" --recent "<changelog bullet>"

Adds a row at the top of raw/notes/_index.md, prepends the bullet to raw/_index.md and the
topic _index.md, refreshes "Last updated", and recounts "Sources:" from the files on disk.
For a note in the reports folder (no wiki) there are no indexes — it says so and exits 0.
"""
import argparse, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wlconfig  # noqa: E402

CFG = wlconfig.load()


def prepend_recent(path, bullet):
    t = open(path).read()
    if "## Recent Changes\n\n" in t:
        t = t.replace("## Recent Changes\n\n", f"## Recent Changes\n\n- {bullet}\n\n", 1)
    else:
        t += f"\n## Recent Changes\n\n- {bullet}\n"
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["index"])
    ap.add_argument("--note", required=True)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--tags", default="worklog")
    ap.add_argument("--recent", required=True)
    a = ap.parse_args()
    note = os.path.abspath(os.path.expanduser(a.note))
    topic = os.path.join(CFG.wiki_hub or "/nonexistent", "topics", CFG.wiki_topic)
    if not note.startswith(os.path.join(topic, "raw", "notes") + os.sep):
        print("note is outside the llm-wiki hub — no indexes to update")
        return
    if not os.path.exists(note):
        sys.exit(f"note not found: {note}")
    today = CFG.now().strftime("%Y-%m-%d")
    f = os.path.basename(note)

    p = os.path.join(topic, "raw", "notes", "_index.md")
    t = open(p).read()
    if f"]({f})" not in t:
        sep = "|------|---------|------|---------|\n"
        row = f"| [{f}]({f}) | {a.summary.replace('|', '/')} | {a.tags} | {today} |\n"
        t = t.replace(sep, sep + row, 1) if sep in t else t + "\n" + row
    t = re.sub(r"^Last updated: .*$", f"Last updated: {today}", t, count=1, flags=re.M)
    open(p, "w").write(t)

    open(os.path.join(topic, "raw", "_index.md"), "w").write(
        prepend_recent(os.path.join(topic, "raw", "_index.md"), f"{today}: {a.recent}"))

    p = os.path.join(topic, "_index.md")
    t = prepend_recent(p, f"{today}: {a.recent}")
    n = sum(1 for d in ("notes", "articles") if os.path.isdir(os.path.join(topic, "raw", d))
            for x in os.listdir(os.path.join(topic, "raw", d)) if x.endswith(".md") and x != "_index.md")
    t = re.sub(r"^Last updated: .*$", f"Last updated: {today}", t, count=1, flags=re.M)
    t = re.sub(r"^- Sources: \d+ raw documents.*$", f"- Sources: {n} raw documents (verified {today} by count)",
               t, count=1, flags=re.M)
    open(p, "w").write(t)
    print(f"indexed {f} · sources: {n}")


if __name__ == "__main__":
    main()
