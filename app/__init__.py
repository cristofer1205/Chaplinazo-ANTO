import json
import os
from pathlib import Path

from flask import Flask

from app.db import ROOT, close_db, seed

DEFAULT_ALLOWED_ORIGINS = [
    "https://antonella.wara-api.xyz",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(DATABASE=os.environ.get("DATABASE", str(ROOT / "sales.sqlite")),
        MAP_PATH=os.environ.get("MAP_PATH", str(ROOT / "data/venue.json")),
        MAX_CONTENT_LENGTH=2048, RATE_LIMIT=30, RATE_WINDOW=60,
        TRUSTED_HOSTS=["antonella.wara-api.xyz", "localhost", "127.0.0.1"],
        ALLOWED_ORIGINS=os.environ.get("ALLOWED_ORIGINS", ",".join(DEFAULT_ALLOWED_ORIGINS)).split(","),
        WRITES_ENABLED=os.environ.get("WRITES_ENABLED", "1") == "1")
    if config:
        app.config.update(config)
    seed(app.config["DATABASE"], json.loads(Path(app.config["MAP_PATH"]).read_text()))
    app.teardown_appcontext(close_db)
    from app.api import install
    install(app)
    return app
