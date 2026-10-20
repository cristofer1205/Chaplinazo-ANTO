CREATE TABLE venue (
    id INTEGER PRIMARY KEY CHECK(id BETWEEN 1 AND 69),
    label TEXT NOT NULL UNIQUE,
    level TEXT NOT NULL CHECK(level IN ('platea','1','2','3','4','5','6')),
    x INTEGER NOT NULL CHECK(x BETWEEN 0 AND 1600),
    y INTEGER NOT NULL CHECK(y BETWEEN 0 AND 1236),
    capacity INTEGER NOT NULL CHECK(capacity > 0),
    UNIQUE(x,y)
) STRICT;
CREATE TABLE state (
    table_id INTEGER PRIMARY KEY REFERENCES venue(id),
    sold INTEGER NOT NULL CHECK(sold >= 0),
    version INTEGER NOT NULL CHECK(version >= 0)
) STRICT;
CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    table_id INTEGER NOT NULL REFERENCES venue(id),
    kind TEXT NOT NULL CHECK(kind IN ('sale','correction')),
    actor TEXT NOT NULL CHECK(length(trim(actor)) BETWEEN 1 AND 80),
    reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 240),
    previous_count INTEGER NOT NULL CHECK(previous_count >= 0),
    new_count INTEGER NOT NULL CHECK(new_count >= 0),
    version INTEGER NOT NULL CHECK(version > 0),
    created_at TEXT NOT NULL,
    UNIQUE(table_id,version)
) STRICT;
CREATE TABLE metadata (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    schema_version INTEGER NOT NULL CHECK(schema_version=1),
    map_hash TEXT NOT NULL,
    verification TEXT NOT NULL
) STRICT;
CREATE TRIGGER state_capacity_insert BEFORE INSERT ON state
WHEN NEW.sold > (SELECT capacity FROM venue WHERE id=NEW.table_id)
BEGIN SELECT RAISE(ABORT,'capacity'); END;
CREATE TRIGGER state_capacity_update BEFORE UPDATE ON state
WHEN NEW.sold > (SELECT capacity FROM venue WHERE id=NEW.table_id)
BEGIN SELECT RAISE(ABORT,'capacity'); END;
CREATE TRIGGER event_consistency BEFORE INSERT ON events
WHEN NEW.new_count > (SELECT capacity FROM venue WHERE id=NEW.table_id)
 OR NOT EXISTS (SELECT 1 FROM state WHERE table_id=NEW.table_id
                AND sold=NEW.new_count AND version=NEW.version)
BEGIN SELECT RAISE(ABORT,'event state mismatch'); END;
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT,'immutable event'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT,'immutable event'); END;
CREATE TRIGGER venue_no_insert BEFORE INSERT ON venue
WHEN EXISTS (SELECT 1 FROM metadata)
BEGIN SELECT RAISE(ABORT,'frozen venue'); END;
CREATE TRIGGER venue_no_update BEFORE UPDATE ON venue
BEGIN SELECT RAISE(ABORT,'frozen venue'); END;
CREATE TRIGGER venue_no_delete BEFORE DELETE ON venue
BEGIN SELECT RAISE(ABORT,'frozen venue'); END;
CREATE TRIGGER metadata_no_update BEFORE UPDATE ON metadata
BEGIN SELECT RAISE(ABORT,'frozen metadata'); END;
CREATE TRIGGER metadata_no_delete BEFORE DELETE ON metadata
BEGIN SELECT RAISE(ABORT,'frozen metadata'); END;
