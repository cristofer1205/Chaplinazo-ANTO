import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest


def state(client):
    return client.get("/api/tables").get_json()


def history(client):
    return client.get("/api/history").get_json()["events"]


@pytest.mark.parametrize("path,method,headers,body,status", [
    ("/../app/schema.sql", "GET", {}, None, 404),
    ("/%2e%2e/app/schema.sql", "GET", {}, None, 404),
    ("/api/tables", "DELETE", {}, None, 405),
    ("/api/tables/1/sales", "PUT", {}, None, 405),
    ("/api/tables", "GET", {"Host": "evil.example"}, None, 400),
    ("/api/tables/1/sales", "POST", {"Origin": "https://evil.example"}, {}, 403),
    ("/api/tables/1/sales", "POST", {"Origin": "null"}, {}, 403),
    ("/api/tables/1/sales", "POST", {}, {"quantity": "1; DROP TABLE state"}, 400),
    ("/api/tables/1/sales", "POST", {}, {"actor": "<script>alert(1)</script>"}, 400),
    ("/api/tables/1/sales", "POST", {}, {"reason": "x" * 5000}, 413),
    ("/api/tables/1/sales", "POST", {}, {"buyer_name": "No buyer fields"}, 400),
])
def test_http_threat_rejection(client, payload, path, method, headers, body, status):
    before, events = state(client), history(client)
    data = None if body is None else {**payload, **body}
    response = client.open(path, method=method, headers=headers, json=data)
    assert response.status_code == status
    assert state(client) == before
    assert history(client) == events


def test_rate_limit_rejection(application, payload):
    application.config["RATE_LIMIT"] = 2
    client = application.test_client()
    before = state(client)
    for _ in range(2):
        assert client.post("/api/tables/1/sales", json={}).status_code == 400
    assert client.post("/api/tables/1/sales", json=payload).status_code == 429
    assert state(client) == before
    assert history(client) == []


def test_xss_csp_and_parameterized_sql(client, payload):
    before = state(client)
    for field, value in [("actor", "<img src=x onerror=alert(1)>"),
                         ("reason", "<script>alert(1)</script>"),
                         ("actor", "Robert'); DROP TABLE venue;--")]:
        assert client.post("/api/tables/1/sales", json={**payload, field: value}).status_code == 400
    assert state(client) == before
    assert history(client) == []
    response = client.get("/")
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert b"<script>alert" not in response.data


@pytest.mark.parametrize("field,value", [("quantity", 0), ("quantity", -1),
    ("quantity", True), ("quantity", 1.0), ("quantity", "1"), ("actor", " "),
    ("reason", ""), ("expected_version", False), ("expected_version", -1)])
def test_validation_no_mutation(client, payload, field, value):
    before = state(client)
    assert client.post("/api/tables/1/sales", json={**payload, field: value}).status_code == 400
    assert state(client) == before and history(client) == []


def test_sales_corrections_capacity_history(client, payload):
    response = client.post("/api/tables/1/sales", json={**payload, "quantity": 4})
    assert response.status_code == 201
    assert response.json["current"]["remaining"] == 0
    assert client.post("/api/tables/1/sales", json=payload).status_code == 409
    assert client.post("/api/tables/1/sales", json={**payload, "expected_version": 1}).status_code == 409
    correction = dict(count=0, actor="Otra persona", reason="Corrección", expected_version=1)
    assert client.post("/api/tables/1/corrections", json=correction).status_code == 201
    correction["expected_version"] = 2
    assert client.post("/api/tables/1/corrections", json=correction).status_code == 201
    events = history(client)
    assert len(events) == 3
    assert [(e["previous_count"], e["new_count"]) for e in events] == [(0, 0), (4, 0), (0, 4)]
    assert all(e["created_at"].endswith("Z") for e in events)
    first = client.get("/api/history?limit=1").json
    assert len(first["events"]) == 1
    assert first["events"][0]["id"] == 3
    older = client.get(f'/api/history?before_id={first["next_before_id"]}&limit=1').json
    assert older["events"][0]["id"] == 2
    assert client.get(f'/api/history?after_id=0&table_id=1').json["events"][0]["id"] == 1


def test_history_page_and_descending_cursor(client, payload):
    assert client.get("/").status_code == 200
    diagram = client.get("/").get_data(as_text=True)
    assert 'href="/history">Historial de cambios' in diagram
    assert 'id="history"' not in diagram
    page = client.get("/history").get_data(as_text=True)
    assert 'href="/">Plano' in page and 'id="history"' in page
    assert client.get("/api/history").json["events"] == []
    for expected_version, actor in enumerate(("Primera", "Segunda", "Tercera")):
        assert client.post("/api/tables/1/sales", json={**payload, "actor": actor,
                            "expected_version": expected_version}).status_code == 201
    newest = client.get("/api/history?limit=2").json
    assert [event["actor"] for event in newest["events"]] == ["Tercera", "Segunda"]
    older = client.get(f'/api/history?before_id={newest["next_before_id"]}&limit=2').json
    assert [event["actor"] for event in older["events"]] == ["Primera"]
    assert not ({event["id"] for event in newest["events"]} & {event["id"] for event in older["events"]})


def test_last_seat_race(application, payload):
    with application.test_client() as c:
        assert c.post("/api/tables/1/sales", json={**payload, "quantity": 3}).status_code == 201
    start = threading.Barrier(2)
    def write(_):
        with application.test_client() as c:
            start.wait(timeout=5)
            return c.post("/api/tables/1/sales", json={**payload, "expected_version": 1}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, range(2))) == [201, 409]
    with application.test_client() as c:
        assert state(c)["tables"][0]["sold"] == 4
        assert len(history(c)) == 2


def test_audit_failure_rollback_and_immutability(application, client, payload):
    from app.db import connect

    db = connect(application.config["DATABASE"])
    db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'fault'); END")
    before = state(client)
    assert client.post("/api/tables/1/sales", json=payload).status_code == 503
    assert state(client) == before and history(client) == []
    db.execute("DROP TRIGGER fail_audit")
    assert client.post("/api/tables/1/sales", json=payload).status_code == 201
    for sql in ["UPDATE events SET actor='other'", "DELETE FROM events",
                "UPDATE venue SET capacity=100", "DELETE FROM venue",
                "UPDATE state SET sold=100"]:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql)
    assert len(history(client)) == 1
    db.close()


def test_database_settings_restart_and_lock(application, client, payload):
    from app import create_app
    from app.db import connect

    db = connect(application.config["DATABASE"])
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert client.post("/api/tables/1/sales", json=payload).status_code == 201
    restarted = create_app(application.config).test_client()
    assert state(restarted) == state(client) and history(restarted) == history(client)
    db.execute("BEGIN IMMEDIATE")
    assert client.post("/api/tables/2/sales", json=payload).status_code == 503
    db.rollback()
    assert len(history(client)) == 1
    db.close()


@pytest.mark.parametrize("path,body,status", [
    ("/api/tables/999/sales", {"quantity": 1}, 404),
    ("/api/tables/1/corrections", {"count": -1}, 400),
    ("/api/tables/1/corrections", {"count": 5}, 409),
    ("/api/tables/1/corrections", {"count": True}, 400),
])
def test_invalid_domain(client, path, body, status):
    before = state(client)
    response = client.post(path, json={**body, "actor": "A", "reason": "R", "expected_version": 0})
    assert response.status_code == status
    assert state(client) == before and history(client) == []


def test_json_and_query_validation(client):
    for data, content in [("{", "application/json"), ("[]", "application/json"),
                          ("{}", "text/plain")]:
        assert client.post("/api/tables/1/sales", data=data, content_type=content).status_code == 400
    for query in ["limit=0", "limit=201", "after_id=-1", "before_id=-1", "after_id=1&before_id=1", "limit=x", "buyer=x", "table_id=0"]:
        assert client.get("/api/history?" + query).status_code == 400
    assert history(client) == []


def test_snapshot_consistency_during_concurrent_commit(application, payload):
    from app.db import connect, snapshot
    from app.service import change

    reader = connect(application.config["DATABASE"])
    writer = connect(application.config["DATABASE"])
    committed = []
    def interleave(sql):
        if "coalesce(max(id)" in sql and not committed:
            change(writer, 1, "sale", payload)
            committed.append(True)
    reader.set_trace_callback(interleave)
    old = snapshot(reader)
    assert committed == [True]
    assert old["sold"] == 0 and old["last_event_id"] == 0
    new = snapshot(reader)
    assert new["sold"] == 1 and new["last_event_id"] == 1
    reader.close()
    writer.close()


def test_request_connections_close_and_writes_can_be_disabled(application, client, payload):
    from app.db import get_db

    with application.app_context():
        first = get_db()
        assert get_db() is first
    with pytest.raises(sqlite3.ProgrammingError):
        first.execute("SELECT 1")
    with application.app_context():
        assert get_db() is not first
    application.config["WRITES_ENABLED"] = False
    assert client.post("/api/tables/1/sales", json=payload).status_code == 503
    assert history(client) == [] and state(client)["sold"] == 0


def test_same_origin_unicode_and_plain_text(client, payload):
    response = client.post("/api/tables/1/sales", json={**payload, "actor": " María O'Connor ", "reason": " Venta confirmada "},
                           headers={"Origin": "https://antonella.wara-api.xyz"})
    assert response.status_code == 201
    assert response.json["event"]["actor"] == "María O'Connor"
    assert response.json["event"]["reason"] == "Venta confirmada"


def test_origin_allowlist_defaults_and_explicit_preview_env(tmp_path, map_data, monkeypatch):
    import json
    from app import DEFAULT_ALLOWED_ORIGINS, create_app

    assert DEFAULT_ALLOWED_ORIGINS == [
        "https://antonella.wara-api.xyz",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]
    assert all(":8001" not in origin for origin in DEFAULT_ALLOWED_ORIGINS)

    venue = tmp_path / "venue.json"
    venue.write_text(json.dumps(map_data))
    monkeypatch.setenv(
        "ALLOWED_ORIGINS",
        "https://antonella.wara-api.xyz,http://localhost:8001,http://127.0.0.1:8001",
    )
    application = create_app(dict(TESTING=True, DATABASE=str(tmp_path / "preview.sqlite"),
                                  MAP_PATH=str(venue), RATE_LIMIT=1000))
    assert application.config["ALLOWED_ORIGINS"] == [
        "https://antonella.wara-api.xyz",
        "http://localhost:8001",
        "http://127.0.0.1:8001",
    ]
    client = application.test_client()
    payload = dict(quantity=1, expected_version=0, actor="Preview operator", reason="Origin test")
    assert client.post("/api/tables/1/sales", json=payload,
                       headers={"Origin": "http://localhost:8001"}).status_code == 201
    assert client.post("/api/tables/2/sales", json=payload,
                       headers={"Origin": "http://localhost:8002"}).status_code == 403
    assert client.post("/api/tables/2/sales", json=payload,
                       headers={"Origin": "http://localhost"}).status_code == 403
