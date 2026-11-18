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
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    lane TEXT NOT NULL DEFAULT 'park',
    position INTEGER DEFAULT 0,
    qty INTEGER DEFAULT 1,
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
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY,
    card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    result TEXT NOT NULL CHECK (result IN ('success','fail')),
    note TEXT DEFAULT '',
    change TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

_lock = threading.Lock()


def init():
    os.makedirs(FILES_DIR, exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)


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
