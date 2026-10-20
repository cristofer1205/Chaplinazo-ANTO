import re
from datetime import datetime, timezone


class Rejected(Exception):
    def __init__(self, message, status=400, current=None):
        self.message, self.status, self.current = message, status, current


def integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**31 - 1:
        raise Rejected("Ingrese un número entero válido.")
    return value


def text(value, maximum):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise Rejected("Complete el nombre y el motivo dentro del límite indicado.")
    value = value.strip()
    # Plain-text attribution, not HTML or executable/query fragments.
    if re.search(r"[<>;\x00-\x1f\x7f]|--", value):
        raise Rejected("Use texto simple sin etiquetas ni caracteres de control.")
    return value


def change(db, table_id, kind, payload):
    field = "quantity" if kind == "sale" else "count"
    if not isinstance(payload, dict) or set(payload) != {field, "expected_version", "actor", "reason"}:
        raise Rejected("Los campos enviados no son válidos.")
    value = integer(payload[field], 1 if kind == "sale" else 0)
    version = integer(payload["expected_version"])
    actor, reason = text(payload["actor"], 80), text(payload["reason"], 240)
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""SELECT venue.*,sold,version,capacity-sold AS remaining
                          FROM venue JOIN state ON id=table_id WHERE id=?""", (table_id,)).fetchone()
        if row is None:
            raise Rejected("La mesa no existe.", 404)
        current = dict(row)
        if current["version"] != version:
            raise Rejected("La mesa cambió. Revise los datos actuales antes de enviar otra vez.", 409, current)
        count = current["sold"] + value if kind == "sale" else value
        if count > current["capacity"]:
            raise Rejected("La cantidad supera la capacidad de la mesa.", 409, current)
        db.execute("UPDATE state SET sold=?,version=version+1 WHERE table_id=?", (count, table_id))
        created = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        event_id = db.execute("""INSERT INTO events
            (table_id,kind,actor,reason,previous_count,new_count,version,created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (table_id, kind, actor, reason, current["sold"], count, version + 1, created)).lastrowid
        event = dict(db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone())
        current.update(sold=count, version=version + 1, remaining=current["capacity"] - count)
        db.commit()
        return dict(event=event, current=current)
    except Exception:
        db.rollback()
        raise
