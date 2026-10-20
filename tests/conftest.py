import hashlib
import json

import pytest


@pytest.fixture
def map_data():
    # Deliberately synthetic: never assert approval of the real source map.
    from app.db import map_hash

    tables = [dict(id=i, label=f"N.{i}", level="platea", x=i * 10, y=400,
                   capacity=4 if i <= 45 else 5, verified=True)
              for i in range(1, 70)]
    return dict(source_sha256=hashlib.sha256(b"synthetic-test-source").hexdigest(),
                coordinate_system=dict(width=1600, height=1236), tables=tables,
                verification=dict(status="approved", reviewer="Synthetic fixture only",
                                  approved_at="2026-10-19T00:00:00Z",
                                  map_sha256=map_hash(tables)))


@pytest.fixture
def application(tmp_path, map_data):
    from app import create_app

    venue = tmp_path / "venue.json"
    venue.write_text(json.dumps(map_data))
    return create_app(dict(TESTING=True, DATABASE=str(tmp_path / "test.sqlite"),
                           MAP_PATH=str(venue), RATE_LIMIT=1000))


@pytest.fixture
def client(application):
    return application.test_client()


@pytest.fixture
def payload():
    return dict(quantity=1, expected_version=0, actor="Vendedor A", reason="Venta")
