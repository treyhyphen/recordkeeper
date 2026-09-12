"""Checkpointed raw-page archives; preserve every returned play occurrence."""

import csv
import json
import os
import sqlite3
import time
from pathlib import Path


def connect(path):
    """Open the versioned local archive database."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY, username TEXT NOT NULL, cutoff INTEGER NOT NULL,
          next_page INTEGER NOT NULL DEFAULT 1, expected INTEGER,
          status TEXT NOT NULL DEFAULT 'running');
        CREATE TABLE IF NOT EXISTS pages (
          run_id INTEGER NOT NULL, page INTEGER NOT NULL, payload TEXT NOT NULL,
          PRIMARY KEY(run_id,page));
        PRAGMA user_version=1;
    """)
    os.chmod(path, 0o600)
    return db


def tracks(payload):
    """Return completed plays without collapsing legitimate repeated listens."""
    items = payload["recenttracks"].get("track", [])
    if isinstance(items, dict):
        items = [items]
    return [
        x
        for x in items
        if "date" in x and x.get("@attr", {}).get("nowplaying") != "true"
    ]


def backup(db, username, fetch, delay=0.3, cutoff=None):
    """Resume a fixed-cutoff snapshot; checkpoint each page atomically."""
    run = db.execute(
        "SELECT id,cutoff,next_page,expected FROM runs WHERE username=? AND status='running' ORDER BY id DESC LIMIT 1",
        (username,),
    ).fetchone()
    if run is None:
        cutoff = cutoff if cutoff is not None else int(time.time()) - 1
        with db:
            cur = db.execute(
                "INSERT INTO runs(username,cutoff) VALUES (?,?)", (username, cutoff)
            )
        run = (cur.lastrowid, cutoff, 1, None)
    run_id, cutoff, page, expected = run
    while True:
        payload = fetch(username, cutoff, page)
        meta = payload["recenttracks"]["@attr"]
        total = int(meta["total"])
        pages = max(1, int(meta["totalPages"]))
        if int(meta["page"]) != page:
            raise ValueError("Provider returned the wrong page; checkpoint unchanged")
        if expected is not None and total != expected:
            raise ValueError(
                "History changed during snapshot; use abandon then start a fresh backup"
            )
        expected = total
        if page < pages and not tracks(payload):
            raise ValueError("Empty intermediate page; checkpoint unchanged")
        with db:
            db.execute(
                "INSERT INTO pages VALUES (?,?,?)", (run_id, page, json.dumps(payload))
            )
            db.execute(
                "UPDATE runs SET next_page=?,expected=? WHERE id=?",
                (page + 1, total, run_id),
            )
        if page >= pages:
            count = sum(
                len(tracks(json.loads(row[0])))
                for row in db.execute(
                    "SELECT payload FROM pages WHERE run_id=?", (run_id,)
                )
            )
            if count != total:
                with db:
                    db.execute(
                        "UPDATE runs SET status='incomplete' WHERE id=?", (run_id,)
                    )
                raise ValueError(f"Archive count mismatch: {count} versus {total}")
            with db:
                db.execute("UPDATE runs SET status='complete' WHERE id=?", (run_id,))
            return run_id, count
        page += 1
        time.sleep(delay)


def export(db, run_id, directory):
    """Export a verified snapshot as raw JSONL pages and human-readable CSV."""
    run = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
    if not run or run[0] != "complete":
        raise ValueError("Only complete snapshots can be exported")
    directory = Path(directory) / f"run-{run_id}"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (
        (directory / "pages.jsonl.tmp").open("w") as raw,
        (directory / "plays.csv.tmp").open("w", newline="") as out,
    ):
        writer = csv.writer(out)
        writer.writerow(["timestamp", "artist", "track", "album", "mbid"])
        for row in db.execute(
            "SELECT payload FROM pages WHERE run_id=? ORDER BY page", (run_id,)
        ):
            raw.write(row[0] + "\n")
            for item in tracks(json.loads(row[0])):
                writer.writerow(
                    [
                        item["date"]["uts"],
                        item["artist"]["#text"],
                        item["name"],
                        item.get("album", {}).get("#text", ""),
                        item.get("mbid", ""),
                    ]
                )
    for name in ("pages.jsonl", "plays.csv"):
        os.replace(directory / (name + ".tmp"), directory / name)
    return directory
