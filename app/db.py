import os
import sqlite3
import threading
from contextlib import contextmanager

DATA_DIR = os.environ.get("KANBAMBU_DATA", os.path.join(os.path.dirname(__file__), "..", "data"))
FILES_DIR = os.path.join(DATA_DIR, "files")
DB_PATH = os.path.join(DATA_DIR, "kanbambu.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT DEFAULT '',
    image_file_id INTEGER,
    bambu_file_id INTEGER,
    bom TEXT DEFAULT '[]',
    position INTEGER DEFAULT 0,
    collapsed INTEGER DEFAULT 0,
    sets INTEGER DEFAULT 1,         -- copies of the whole project to print
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    lane TEXT NOT NULL DEFAULT 'analysed',
    position INTEGER DEFAULT 0,
    qty INTEGER DEFAULT 1,          -- copies wanted
    per_plate INTEGER DEFAULT 1,    -- copies per plate
    plate_index INTEGER,            -- plate number in the project's Bambu file, when imported from it
    url TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    preview_file_id INTEGER,
    analysis TEXT,
    pending_change TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    card_id INTEGER REFERENCES cards(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    stored TEXT NOT NULL,
    size INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS epics (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT DEFAULT '',
    image_file_id INTEGER,
    sets INTEGER DEFAULT 1,         -- copies of the whole epic (multiplies its projects' copies)
    position INTEGER DEFAULT 0,     -- shares the top-level order with projects outside any epic
    collapsed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS parts (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    per_set INTEGER DEFAULT 1,      -- needed per copy of the project
    aliases TEXT DEFAULT '[]',      -- other object names that mean this part
    position INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS card_parts (
    card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    part_id INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    qty INTEGER NOT NULL,
    PRIMARY KEY (card_id, part_id)
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY,
    card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    result TEXT NOT NULL CHECK (result IN ('success','fail')),
    note TEXT DEFAULT '',
    change TEXT DEFAULT '',
    parts TEXT DEFAULT '{}',        -- {part_id: [ok, planned]}
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

_lock = threading.Lock()


def init():
    os.makedirs(FILES_DIR, exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)
        # migrations for older databases
        for table, col, ddl in (("cards", "per_plate", "INTEGER DEFAULT 1"),
                                ("cards", "plate_index", "INTEGER"),
                                ("projects", "sets", "INTEGER DEFAULT 1"),
                                ("attempts", "parts", "TEXT DEFAULT '{}'"),
                                ("projects", "epic_id", "INTEGER"),
                                ("projects", "folder", "TEXT DEFAULT ''"),      # '' = default folder
                                ("projects", "bambu_src", "TEXT"),             # file in the folder it came from
                                ("projects", "bambu_mtime", "REAL"),
                                ("projects", "slicing_src", "TEXT"),
                                ("projects", "slicing_mtime", "REAL"),
                                ("epics", "folder", "TEXT DEFAULT ''")):
            if col not in {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        # the Park lane was merged into the queue ("analysed")
        c.execute("UPDATE cards SET lane='analysed' WHERE lane='park'")


@contextmanager
def conn():
    with _lock:
        c = sqlite3.connect(DB_PATH)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()
