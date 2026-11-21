"""Watch the epic and project folders, and bring in what gets saved there.

Polling (no extra dependency), every few seconds. The first time a folder is seen, its files are
only recorded; from then on, a new or re-saved file is handled once it has stopped changing:

Project folder
  - the project's Bambu file or slicing export, saved again  -> re-read
  - a new .3mf, and the project has no Bambu file yet         -> becomes its Bambu file
  - a .gcode.3mf with several plates (all plates export)      -> slicing of the plate cards
  - a .gcode.3mf of one plate, or an STL/STEP/OBJ             -> attached to the card of the same name
                                                                 (file name or title), else a new card
Epic folder
  - a new .3mf that no project uses yet                       -> a new project in the epic
"""
from __future__ import annotations

import os
import re
import threading
import time
from collections import deque

from . import db
from . import folders as fld

INTERVAL = float(os.environ.get("KANBAMBU_WATCH_INTERVAL", "4"))
_pending: dict = {}           # (scope, path) -> (mtime, size) seen on the previous pass, not yet handled
events: deque = deque(maxlen=40)
_seq = 0
_lock = threading.Lock()


def note(msg: str):
    global _seq
    with _lock:
        _seq += 1
        events.append({"id": _seq, "t": time.time(), "msg": msg})


def stem(name: str) -> str:
    return re.sub(r"\.gcode\.3mf$|\.[^.]+$", "", os.path.basename(name), flags=re.I).strip().lower()


def enabled() -> bool:
    if os.environ.get("KANBAMBU_WATCH", "1") == "0":
        return False
    with db.conn() as c:
        return fld.get_setting(c, "watch", "1") != "0"


# Each project and each epic keeps its own record of the files it has seen ("scope": p12, e3),
# so a folder shared by an epic and its projects is handled correctly by each.

def _known(c, scope, path):
    return c.execute("SELECT * FROM folder_files WHERE scope=? AND path=?", (scope, path)).fetchone()


def _remember(c, scope, path, st, card_id=None):
    c.execute("INSERT INTO folder_files (scope, path, mtime, size, card_id) VALUES (?,?,?,?,?) "
              "ON CONFLICT(scope, path) DO UPDATE SET mtime=excluded.mtime, size=excluded.size, "
              "card_id=COALESCE(excluded.card_id, folder_files.card_id)",
              (scope, path, st.st_mtime, st.st_size, card_id))


def _targets():
    """(kind, row, folder) for every epic and project, projects first."""
    with db.conn() as c:
        out = [("project", p, fld.project_folder(c, p)) for p in c.execute("SELECT * FROM projects")]
        out += [("epic", e, fld.epic_folder(c, e)) for e in c.execute("SELECT * FROM epics")]
    return out


def scan_once(m, stable_wait: bool = True):
    """One pass. `m` is app.main (ingest, imports, cards). Returns the number of files handled."""
    handled = 0
    for kind, row, folder in _targets():
        if not os.path.isdir(folder):
            continue
        scope = f"{kind[0]}{row['id']}"
        with db.conn() as c:
            first = c.execute("SELECT 1 FROM folder_seen WHERE scope=? AND path=?", (scope, folder)).fetchone() is None
            if first:
                c.execute("INSERT OR IGNORE INTO folder_seen (scope, path) VALUES (?,?)", (scope, folder))
        for f in fld.list_models(folder):
            if kind == "epic" and f["kind"] != "3mf":
                continue
            full = os.path.join(folder, f["name"])
            try:
                st = os.stat(full)
            except OSError:
                continue
            with db.conn() as c:
                k = _known(c, scope, full)
                if first:
                    _remember(c, scope, full, st)   # baseline: already there before watching this folder
                    continue
            if k and k["mtime"] == st.st_mtime and k["size"] == st.st_size:
                _pending.pop((scope, full), None)
                continue
            # wait until Bambu Studio has finished writing (same size and time on two passes)
            sig = (st.st_mtime, st.st_size)
            if stable_wait and _pending.get((scope, full)) != sig:
                _pending[(scope, full)] = sig
                continue
            _pending.pop((scope, full), None)
            try:
                if kind == "project":
                    _project_file(m, row, scope, folder, full, f["kind"], k, st)
                else:
                    _epic_file(m, row, scope, folder, full, st)
                handled += 1
            except Exception as e:  # a bad file must not stop the watcher
                note(f"⚠ {os.path.basename(full)} : {e}")
                with db.conn() as c:
                    _remember(c, scope, full, st)
    return handled


def _project_file(m, p, scope, folder, full, kind, known, st):
    pid = p["id"]
    name = os.path.basename(full)
    rel = os.path.relpath(full, folder)
    with db.conn() as c:
        p = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        sharing = sum(1 for q in c.execute("SELECT * FROM projects") if fld.project_folder(c, q) == folder)
    # A folder shared by several projects (e.g. an epic's folder holding all its .3mf): a new file
    # belongs to this project only if it is named like it ("stacker.gcode.3mf" for "stacker.3mf").
    own = stem(p["bambu_src"] or p["name"])
    if sharing > 1 and full not in (p["bambu_src"], p["slicing_src"]) and not stem(full).startswith(own):
        with db.conn() as c:
            _remember(c, scope, full, st)
        return
    # 1. the files this project was imported from, saved again
    for role in ("bambu", "slicing"):
        if full == p[f"{role}_src"]:
            m.import_from_folder(pid, folder, rel, role)
            note(f"↻ {p['name']} : {name} relu")
            with db.conn() as c:
                _remember(c, scope, full, st)
            return
    # 2. a project file for a project that has none
    if kind == "3mf":
        if not p["bambu_file_id"]:
            m.import_from_folder(pid, folder, rel, "bambu")
            note(f"➕ {p['name']} : plateaux de {name}")
        with db.conn() as c:
            _remember(c, scope, full, st)
        return
    # 3. an all-plates slicing export
    if kind == "gcode3mf":
        info = m.parse_3mf(full)
        sliced = [x for x in info["plates"] if x["analysis"]]
        with db.conn() as c:
            plate_cards = {r["plate_index"] for r in c.execute(
                "SELECT plate_index FROM cards WHERE project_id=? AND plate_index IS NOT NULL", (pid,))}
        if len(sliced) > 1 or (sliced and not known and plate_cards and stem(name) not in _card_stems(pid)):
            m.import_from_folder(pid, folder, rel, "slicing")
            note(f"⏱ {p['name']} : slicing de {len(sliced)} plateau(x)")
            with db.conn() as c:
                _remember(c, scope, full, st)
            return
    # 4. a model or one sliced plate: the card of the same name, or a new one
    cid = known["card_id"] if known and known["card_id"] else _card_for(pid, name)
    with db.conn() as c:
        if cid and not c.execute("SELECT 1 FROM cards WHERE id=?", (cid,)).fetchone():
            cid = None
    new = cid is None
    if new:
        cid = m.create_card(m.CardIn(project_id=pid, title=re.sub(r"\.gcode\.3mf$|\.[^.]+$", "", name, flags=re.I)))["id"]
    with db.conn() as c:  # replace the previous copy of this file on the card
        for r in c.execute("SELECT id FROM files WHERE card_id=? AND name=?", (cid, m.safe_name(name))).fetchall():
            m.delete_file_row(c, r["id"])
    with open(full, "rb") as fh:
        m.ingest(fh.read(), name, card_id=cid, role="attach")
    with db.conn() as c:
        title = c.execute("SELECT title FROM cards WHERE id=?", (cid,)).fetchone()["title"]
        _remember(c, scope, full, st, card_id=cid)
    note(f"{'➕' if new else '↻'} {p['name']} › {title} : {name}")


def _card_stems(pid) -> set:
    with db.conn() as c:
        return {stem(r["name"]) for r in c.execute(
            "SELECT f.name FROM files f JOIN cards k ON k.id=f.card_id WHERE k.project_id=?", (pid,))} | \
               {r["title"].strip().lower() for r in c.execute("SELECT title FROM cards WHERE project_id=?", (pid,))}


def _card_for(pid, name):
    """Card of the project whose attached file or title has this file's name (without extension)."""
    s = stem(name)
    with db.conn() as c:
        for r in c.execute("SELECT k.id, f.name FROM cards k JOIN files f ON f.card_id=k.id "
                           "WHERE k.project_id=? ORDER BY k.id DESC", (pid,)):
            if stem(r["name"]) == s:
                return r["id"]
        for r in c.execute("SELECT id, title FROM cards WHERE project_id=? ORDER BY id DESC", (pid,)):
            if r["title"].strip().lower() == s:
                return r["id"]
    return None


def _epic_file(m, e, scope, folder, full, st):
    with db.conn() as c:
        used = c.execute("SELECT id FROM projects WHERE bambu_src=?", (full,)).fetchone()
        _remember(c, scope, full, st)
    if used:
        return
    rel = os.path.relpath(full, folder)
    m.epic_import(e["id"], m.EpicImport(names=[rel]))
    note(f"➕ {e['name']} : nouveau projet {os.path.basename(full)}")


def start(m):
    """Background thread for the running server."""
    def loop():
        while True:
            time.sleep(INTERVAL)
            try:
                if enabled() and scan_once(m):
                    m.bump()
            except Exception as e:  # keep watching whatever happens
                note(f"⚠ surveillance : {e}")
    threading.Thread(target=loop, name="kanbambu-watch", daemon=True).start()
