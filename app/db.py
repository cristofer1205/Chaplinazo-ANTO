import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from flask import current_app, g

ROOT = Path(__file__).resolve().parent.parent
LEVELS = {"platea", "1", "2", "3", "4", "5", "6"}


def map_hash(tables):
    return hashlib.sha256(json.dumps(sorted(tables, key=lambda t: t["id"]), sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def validate_map(data):
    tables = data.get("tables")
    if not isinstance(tables, list) or len(tables) != 69:
        raise ValueError("Map must contain exactly 69 assignments")
    if data.get("coordinate_system") != {"width": 1600, "height": 1236}:
        raise ValueError("Invalid coordinate system")
    positions, ids = set(), set()
    for t in tables:
        if not isinstance(t, dict) or set(t) != {"id", "label", "level", "x", "y", "capacity", "verified"}:
            raise ValueError("Invalid table fields")
        if type(t["id"]) is not int or not 1 <= t["id"] <= 69 or t["id"] in ids:
            raise ValueError("Invalid or duplicate table ID")
        if t["label"] != f'N.{t["id"]}' or t["level"] not in LEVELS or t["verified"] is not True:
            raise ValueError("Unverified assignment")
        if type(t["capacity"]) is not int or not 1 <= t["capacity"] <= 300:
            raise ValueError("Invalid capacity")
        if any(type(t[k]) is not int or not 0 <= t[k] <= upper for k, upper in [("x", 1600), ("y", 1236)]):
            raise ValueError("Invalid coordinates")
        point = (t["x"], t["y"])
        if point in positions:
            raise ValueError("Conflicting positions")
        positions.add(point)
        ids.add(t["id"])
    if ids != set(range(1, 70)) or sum(t["capacity"] for t in tables) != 300:
        raise ValueError("Map IDs or total mismatch")
    if not re.fullmatch(r"[0-9a-f]{64}", data.get("source_sha256", "")):
        raise ValueError("Missing source hash")
    approval = data.get("verification", {})
    if (approval.get("status") != "approved" or not isinstance(approval.get("reviewer"), str)
            or not approval["reviewer"].strip() or not approval.get("approved_at")):
        raise ValueError("Independent human map approval is required")
    try:
        date = datetime.fromisoformat(approval["approved_at"].replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError("Approval timestamp must include timezone")
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid approval timestamp") from exc
    if approval.get("map_sha256") != map_hash(tables):
        raise ValueError("Approved map hash mismatch")
    return map_hash(tables)


def connect(path):
    db = sqlite3.connect(path, timeout=0.25, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA busy_timeout=250")
    return db


def seed(path, data):
    digest = validate_map(data)  # Validate before opening/creating any database.
    with closing(connect(path)) as db:
        try:
            # Schema creation and seed share one exclusive writer transaction.
            db.execute("BEGIN IMMEDIATE")
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='metadata'").fetchone()
            if exists:
                stored = db.execute("SELECT * FROM metadata WHERE singleton=1").fetchone()
                expected = [(t["id"], t["label"], t["level"], t["x"], t["y"], t["capacity"])
                            for t in sorted(data["tables"], key=lambda t: t["id"])]
                actual = [tuple(row) for row in db.execute("SELECT * FROM venue ORDER BY id")]
                if (stored is None or stored["schema_version"] != 1 or stored["map_hash"] != digest
                        or actual != expected or db.execute("SELECT count(*) FROM state").fetchone()[0] != 69):
                    raise ValueError("Existing database map/schema mismatch; never reset")
            else:
                # Execute individual statements without executescript's implicit commit.
                statement = ""
                for line in (ROOT / "app/schema.sql").read_text().splitlines(True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        db.execute(statement)
                        statement = ""
                db.executemany("INSERT INTO venue VALUES (:id,:label,:level,:x,:y,:capacity)", data["tables"])
                db.executemany("INSERT INTO state VALUES (?,0,0)", [(t["id"],) for t in data["tables"]])
                db.execute("INSERT INTO metadata VALUES (1,1,?,?)", (digest, json.dumps({
                    "source_sha256": data["source_sha256"], **data["verification"]}, sort_keys=True)))
            db.commit()
        except Exception:
            db.rollback()
            raise


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def snapshot(db):
    try:
        db.execute("BEGIN")
        tables = [dict(row) for row in db.execute("""SELECT venue.*,sold,version,
                  capacity-sold AS remaining FROM venue JOIN state ON id=table_id ORDER BY id""")]
        last_id = db.execute("SELECT coalesce(max(id),0) FROM events").fetchone()[0]
        db.commit()
        return dict(tables=tables, last_event_id=last_id, capacity=300,
                    sold=sum(t["sold"] for t in tables))
    except Exception:
        db.rollback()
        raise
