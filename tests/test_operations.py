import sqlite3
import subprocess
import sys

import pytest


def contents(path):
    with sqlite3.connect(path) as db:
        return {name: db.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
                for name in ("venue", "state", "events", "metadata")}


def test_backup_restore_restart(application, client, payload, tmp_path):
    from app import create_app
    from app.db import connect
    from tools.backup import restore, snapshot

    source = application.config["DATABASE"]
    anchor = connect(source)  # Keep uncheckpointed WAL state alive during backup.
    assert client.post("/api/tables/1/sales", json=payload).status_code == 201
    backup, restored = tmp_path / "backup.sqlite", tmp_path / "restored.sqlite"
    snapshot(source, backup)
    anchor.close()
    expected = contents(source)
    assert contents(backup) == expected
    assert backup.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError): snapshot(source, backup)
    with pytest.raises(ValueError): restore(backup, restored)
    # A later live write must not leak into the earlier backup.
    assert client.post("/api/tables/2/sales", json=payload).status_code == 201
    restore(backup, restored, stopped=True)
    assert contents(restored) == expected
    config = {**application.config, "DATABASE": str(restored)}
    restarted = create_app(config).test_client()
    assert restarted.get("/api/tables").json["sold"] == 1
    assert len(restarted.get("/api/history").json["events"]) == 1
    assert contents(restored) == expected
    run = subprocess.run([sys.executable, "-m", "tools.backup", "verify", str(restored)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr


def test_corrupt_snapshot_refused(application, tmp_path):
    from tools.backup import snapshot

    with sqlite3.connect(application.config["DATABASE"]) as db:
        db.execute("UPDATE state SET sold=1 WHERE table_id=1")
    target = tmp_path / "bad.sqlite"
    with pytest.raises(ValueError, match="history"):
        snapshot(application.config["DATABASE"], target)
    assert not target.exists()
