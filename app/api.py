import sqlite3
import threading
import time
from collections import deque

from flask import jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from app.db import get_db, snapshot
from app.service import Rejected, change


def install(app):
    attempts, lock = deque(), threading.Lock()

    @app.before_request
    def boundary():
        if request.method not in {"GET", "POST"}:
            return jsonify(error="Método no permitido."), 405
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if ((origin is not None and origin not in app.config["ALLOWED_ORIGINS"])
                    or request.headers.get("Sec-Fetch-Site") == "cross-site"):
                return jsonify(error="Origen no permitido."), 403
            if not app.config["WRITES_ENABLED"]:
                return jsonify(error="Los cambios están temporalmente deshabilitados."), 503
            # Global, bounded limiter; independent of attacker-supplied proxy/IP headers.
            with lock:
                now = time.monotonic()
                while attempts and attempts[0] <= now - app.config["RATE_WINDOW"]:
                    attempts.popleft()
                if len(attempts) >= app.config["RATE_LIMIT"]:
                    return jsonify(error="Demasiados intentos. Espere un minuto."), 429, {"Retry-After": "60"}
                attempts.append(now)

    @app.after_request
    def secure(response):
        response.headers.update({
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY", "Cache-Control": "no-store"})
        return response

    @app.errorhandler(Rejected)
    def rejected(error):
        return jsonify(error=error.message, current=error.current), error.status

    @app.errorhandler(sqlite3.Error)
    def unavailable(_error):
        return jsonify(error="No se pudo guardar o leer. Actualice antes de volver a intentar."), 503

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error={400: "Solicitud no válida.", 404: "Recurso no encontrado.",
                              405: "Método no permitido.", 413: "Solicitud demasiado grande."}
                       .get(error.code, "No se pudo procesar la solicitud.")), error.code

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/history")
    def history_page():
        return render_template("history.html")

    @app.get("/api/tables")
    def tables():
        return jsonify(snapshot(get_db()))

    @app.get("/api/history")
    def history():
        if set(request.args) - {"after_id", "before_id", "limit", "table_id"} or any(len(v) != 1 for _, v in request.args.lists()):
            raise Rejected("Parámetros no válidos.")
        def number(key, default, minimum, maximum):
            value = request.args.get(key, str(default))
            if not value.isascii() or not value.isdecimal() or len(value) > 10 or not minimum <= int(value) <= maximum:
                raise Rejected("Parámetros no válidos.")
            return int(value)
        if "after_id" in request.args and "before_id" in request.args:
            raise Rejected("Parámetros no válidos.")
        cursor_key = "after_id" if "after_id" in request.args else "before_id"
        cursor = number(cursor_key, 0, 0, 2**31 - 1) if cursor_key in request.args else None
        limit = number("limit", 100, 1, 200)
        table_id = number("table_id", 1, 1, 69) if "table_id" in request.args else None
        if cursor_key == "after_id":
            rows = get_db().execute("""SELECT * FROM events WHERE id>?
                AND (? IS NULL OR table_id=?) ORDER BY id ASC LIMIT ?""",
                (cursor or 0, table_id, table_id, limit + 1)).fetchall()
            events = [dict(row) for row in rows[:limit]]
            return jsonify(events=events, next_after_id=events[-1]["id"] if events else (cursor or 0),
                           has_more=len(rows) > limit)
        condition = "id<?" if cursor is not None else "1=1"
        rows = get_db().execute(f"""SELECT * FROM events WHERE {condition}
            AND (? IS NULL OR table_id=?) ORDER BY id DESC LIMIT ?""",
            ((cursor,) if cursor is not None else ()) + (table_id, table_id, limit + 1)).fetchall()
        events = [dict(row) for row in rows[:limit]]
        return jsonify(events=events, next_before_id=events[-1]["id"] if events else cursor,
                       has_more=len(rows) > limit)

    def write(table_id, kind):
        if request.mimetype != "application/json":
            raise Rejected("Envíe contenido JSON.")
        payload = request.get_json()
        return jsonify(change(get_db(), table_id, kind, payload)), 201

    app.add_url_rule("/api/tables/<int:table_id>/sales", "sales",
                     lambda table_id: write(table_id, "sale"), methods=["POST"])
    app.add_url_rule("/api/tables/<int:table_id>/corrections", "corrections",
                     lambda table_id: write(table_id, "correction"), methods=["POST"])
