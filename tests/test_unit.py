import copy
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest


@pytest.mark.parametrize("case", ["missing", "duplicate", "unknown", "capacity", "total",
    "coordinate", "level", "entry_unverified", "approval", "hash", "source", "conflict"])
def test_map_gate(map_data, case):
    from app.db import validate_map

    data = copy.deepcopy(map_data)
    if case == "missing": data["tables"].pop()
    if case == "duplicate": data["tables"][1]["id"] = 1
    if case == "unknown": data["tables"][1]["id"] = 70
    if case == "capacity": data["tables"][0]["capacity"] = True
    if case == "total": data["tables"][0]["capacity"] = 3
    if case == "coordinate": data["tables"][0]["x"] = -1
    if case == "level": data["tables"][0]["level"] = "unknown"
    if case == "entry_unverified": data["tables"][0]["verified"] = False
    if case == "approval": data["verification"]["status"] = "pending"
    if case == "hash": data["verification"]["map_sha256"] = "0" * 64
    if case == "source": data["source_sha256"] = "bad"
    if case == "conflict": data["tables"][1]["x"] = data["tables"][0]["x"]
    with pytest.raises(ValueError): validate_map(data)


def test_verified_map_nonresetting_seed(tmp_path, map_data):
    from app.db import connect, seed, validate_map

    assert validate_map(map_data)
    path = tmp_path / "seed.sqlite"
    seed(path, map_data)
    db = connect(path)
    db.execute("UPDATE state SET sold=1,version=1 WHERE table_id=1")
    db.close()
    seed(path, map_data)
    db = connect(path)
    assert db.execute("SELECT sold FROM state WHERE table_id=1").fetchone()[0] == 1
    db.close()


def test_user_approved_real_map_seeds_exact_assignments(tmp_path):
    from app import create_app
    from app.db import connect, validate_map

    data = json.loads(Path("data/venue.json").read_text())
    # Freeze the exact transcription approved in chat, not just its capacity total.
    digest = "e8a5ddf957f51a4720e27f4baa9d30bda5de4f7f5d5299a4f290f0ae7e3069c2"
    assert validate_map(data) == digest
    assert data["source_sha256"] == "c469e87017a8f662fd98fd432a426af95d83aca6276e11d0947db7c46a7d1add"
    assert data["verification"]["reviewer"] == "user-approved-in-chat"
    path = tmp_path / "approved.sqlite"
    application = create_app(dict(TESTING=True, DATABASE=str(path)))
    response = application.test_client().get("/api/tables")
    assert response.status_code == 200
    state = response.get_json()
    assert len(state["tables"]) == 69
    assert state["capacity"] == sum(t["capacity"] for t in state["tables"]) == 300
    assert state["sold"] == state["last_event_id"] == 0
    assert state["tables"] == [
        {**{k: v for k, v in t.items() if k != "verified"},
         "sold": 0, "version": 0, "remaining": t["capacity"]}
        for t in data["tables"]
    ]
    with closing(connect(path)) as db:
        metadata = db.execute("SELECT * FROM metadata").fetchone()
        assert metadata["map_hash"] == digest
        assert json.loads(metadata["verification"]) == {
            "source_sha256": data["source_sha256"], **data["verification"]}
    # Restart must accept the same approved map without changing its snapshot.
    restarted = create_app(dict(TESTING=True, DATABASE=str(path)))
    assert restarted.test_client().get("/api/tables").get_json() == state


@pytest.mark.parametrize("case,error", [
    ("pending", "approval"), ("hash", "hash mismatch"),
    ("position", "hash mismatch"), ("capacity", "hash mismatch"),
    ("entry_unverified", "Unverified assignment"),
])
def test_unapproved_or_mismatched_real_map_blocks_startup(tmp_path, case, error):
    from app import create_app
    from app.db import validate_map

    data = json.loads(Path("data/venue.json").read_text())
    if case == "pending": data["verification"]["status"] = "pending"
    if case == "hash": data["verification"]["map_sha256"] = "0" * 64
    if case == "position": data["tables"][0]["x"] += 1
    if case == "capacity":
        # Preserve 300 total: approval must bind individual assignments too.
        data["tables"][0]["capacity"] += 1
        data["tables"][1]["capacity"] -= 1
    if case == "entry_unverified": data["tables"][0]["verified"] = False
    venue = tmp_path / "rejected-map.json"
    venue.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=error):
        validate_map(data)
    with pytest.raises(ValueError, match=error):
        create_app(dict(DATABASE=str(tmp_path / "blocked.sqlite"), MAP_PATH=str(venue)))
    assert not (tmp_path / "blocked.sqlite").exists()


def test_seed_schema_is_atomic_and_existing_map_changes_refused(tmp_path, map_data, monkeypatch):
    from app import db

    original = db.connect
    path = tmp_path / "atomic.sqlite"
    def fail_seed(path):
        connection = original(path)
        def authorizer(action, arg1, _arg2, _database, _trigger):
            import sqlite3
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and arg1 == "state" else sqlite3.SQLITE_OK
        connection.set_authorizer(authorizer)
        return connection
    monkeypatch.setattr(db, "connect", fail_seed)
    with pytest.raises(sqlite3.DatabaseError): db.seed(path, map_data)
    monkeypatch.setattr(db, "connect", original)
    connection = db.connect(path)
    assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    connection.close()
    db.seed(path, map_data)
    changed = copy.deepcopy(map_data)
    changed["tables"][0]["x"] = 11
    changed["verification"]["map_sha256"] = db.map_hash(changed["tables"])
    with pytest.raises(ValueError, match="never reset"): db.seed(path, changed)
