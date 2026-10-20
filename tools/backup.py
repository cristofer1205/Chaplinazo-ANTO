"""SQLite backup API snapshots and non-overwriting, stopped restores."""
import argparse
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path


def verify(db):
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Database integrity check failed")
    from app.db import map_hash, validate_map

    meta = db.execute("SELECT schema_version,map_hash,verification FROM metadata WHERE singleton=1").fetchone()
    if not meta or meta[0] != 1:
        raise ValueError("Unsupported snapshot schema")
    approval = json.loads(meta[2])
    tables = [dict(zip(("id", "label", "level", "x", "y", "capacity"), row), verified=True)
              for row in db.execute("SELECT * FROM venue ORDER BY id")]
    validate_map(dict(tables=tables, coordinate_system=dict(width=1600, height=1236),
                      source_sha256=approval["source_sha256"], verification=approval))
    if map_hash(tables) != meta[1]:
        raise ValueError("Snapshot map hash mismatch")
    counts = {t["id"]: (0, 0) for t in tables}
    for table_id, old, new, version in db.execute("SELECT table_id,previous_count,new_count,version FROM events ORDER BY id"):
        previous, previous_version = counts[table_id]
        if old != previous or version != previous_version + 1 or not 0 <= new <= tables[table_id - 1]["capacity"]:
            raise ValueError("Audit chain mismatch")
        counts[table_id] = (new, version)
    states = {row[0]: (row[1], row[2]) for row in db.execute("SELECT * FROM state ORDER BY table_id")}
    if states != counts:
        raise ValueError("Counts do not match history")


def snapshot(source, destination):
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if source == destination.resolve():
        raise ValueError("Source and destination must differ")
    if any(Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("Destination has stale SQLite sidecars")
    # Never overwrite a snapshot or live database; private files by construction.
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)) as src:
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst)
                verify(dst)
                # Standalone snapshot: no WAL sidecars are required to restore it.
                dst.execute("PRAGMA journal_mode=DELETE")
        with destination.open("rb") as saved:
            os.fsync(saved.fileno())
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def restore(source, destination, *, stopped=False):
    if not stopped:
        raise ValueError("Stop all app processes first and explicitly confirm stopped=True")
    snapshot(source, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["snapshot", "restore", "verify"])
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path, nargs="?")
    parser.add_argument("--stopped", action="store_true")
    args = parser.parse_args()
    if args.operation == "verify":
        with closing(sqlite3.connect(args.source.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            verify(db)
    elif args.destination is None:
        parser.error("destination is required")
    elif args.operation == "restore":
        restore(args.source, args.destination, stopped=args.stopped)
    else:
        snapshot(args.source, args.destination)
    print("Verified operation completed.")


if __name__ == "__main__":
    main()
