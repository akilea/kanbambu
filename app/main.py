import json
import math
import os
import re
import shutil
import uuid
from typing import Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .threemf import merge, parse_3mf, part_name

# "analysed" is shown as the Queue: everything waiting to be printed, sliced or not.
LANES = ("analysed", "printing", "done")
STATIC = os.path.join(os.path.dirname(__file__), "..", "static")

app = FastAPI(title="kanbambu")
db.init()


# ---------- helpers ----------

def kind_of(name: str) -> str:
    n = name.lower()
    if n.endswith(".gcode.3mf"):
        return "gcode3mf"
    ext = n.rsplit(".", 1)[-1] if "." in n else ""
    if ext in ("png", "jpg", "jpeg", "webp", "gif"):
        return "image"
    if ext in ("stl", "3mf", "step", "stp", "obj", "f3d", "scad"):
        return ext.replace("stp", "step")
    return "other"


def safe_name(name: str) -> str:
    name = os.path.basename(name or "file")
    return re.sub(r"[^\w.\- ()]+", "_", name)[:200] or "file"


def file_row(r) -> dict:
    return {"id": r["id"], "name": r["name"], "kind": r["kind"], "size": r["size"],
            "url": f"/files/{r['id']}/{r['name']}"}


def store_bytes(data: bytes, name: str, project_id=None, card_id=None) -> int:
    stored = uuid.uuid4().hex + os.path.splitext(name)[1].lower()
    with open(os.path.join(db.FILES_DIR, stored), "wb") as f:
        f.write(data)
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO files (project_id, card_id, name, kind, stored, size) VALUES (?,?,?,?,?,?)",
            (project_id, card_id, name, kind_of(name), stored, len(data)))
        return cur.lastrowid


def delete_file_row(c, fid: int):
    r = c.execute("SELECT stored FROM files WHERE id=?", (fid,)).fetchone()
    if r:
        try:
            os.remove(os.path.join(db.FILES_DIR, r["stored"]))
        except OSError:
            pass
        c.execute("DELETE FROM files WHERE id=?", (fid,))


def card_dict(c, r) -> dict:
    files = [file_row(f) for f in c.execute(
        "SELECT * FROM files WHERE card_id=? AND id IS NOT ? ORDER BY id",
        (r["id"], r["preview_file_id"]))]
    attempts = []
    for a in c.execute("SELECT * FROM attempts WHERE card_id=? ORDER BY id", (r["id"],)):
        a = dict(a)
        a["parts"] = json.loads(a.get("parts") or "{}")
        a["partial"] = a["result"] == "success" and any(ok < plan for ok, plan in a["parts"].values())
        attempts.append(a)
    comp = [dict(x) for x in c.execute(
        "SELECT cp.part_id, cp.qty, p.name FROM card_parts cp JOIN parts p ON p.id=cp.part_id "
        "WHERE cp.card_id=? ORDER BY p.position, p.id", (r["id"],))]
    ok = sum(a["result"] == "success" for a in attempts)
    per = max(1, r["per_plate"] or 1)
    # a card with a composition is a single print run; plates only apply to simple cards
    if comp:
        # A card with a composition is one print run: still to print unless it sits in Done,
        # however many times it was printed before (a plate queued again for the next copy).
        total, left = 1, (0 if r["lane"] == "done" else 1)
    else:
        total = max(1, math.ceil((r["qty"] or 1) / per))
        left = max(0, total - ok)
    return {
        "id": r["id"], "project_id": r["project_id"], "title": r["title"], "lane": r["lane"],
        "qty": r["qty"], "per_plate": per, "url": r["url"], "notes": r["notes"],
        "plates": total, "plates_done": total - left, "plates_left": left,
        "plate_index": r["plate_index"],
        "preview": f"/files/{r['preview_file_id']}/preview" if r["preview_file_id"] else None,
        "preview_file_id": r["preview_file_id"],
        "analysis": (json.loads(r["analysis"]) or None) if r["analysis"] else None,
        "pending_change": r["pending_change"],
        "files": files, "attempts": attempts, "parts": comp,
        "ok": ok,
        "ko": sum(a["result"] == "fail" for a in attempts),
    }


def project_dict(c, p) -> dict:
    cards = [card_dict(c, r) for r in c.execute(
        "SELECT * FROM cards WHERE project_id=? ORDER BY position, id", (p["id"],))]
    bambu = c.execute("SELECT * FROM files WHERE id=?", (p["bambu_file_id"],)).fetchone() \
        if p["bambu_file_id"] else None
    return {
        "id": p["id"], "name": p["name"], "url": p["url"], "collapsed": bool(p["collapsed"]),
        "sets": p["sets"] or 1,
        "parts": inventory(c, p, cards),
        "image": f"/files/{p['image_file_id']}/image" if p["image_file_id"] else None,
        "bambu": file_row(bambu) if bambu else None,
        "bom": json.loads(p["bom"] or "[]"),
        "cards": cards,
        "stats": project_stats(cards),
    }


def inventory(c, p, cards: list) -> list:
    """Per part: needed, produced (successful pieces), in the pipeline (cards not done), still to plan."""
    sets = p["sets"] or 1
    produced: dict = {}
    pipeline: dict = {}
    for k in cards:
        for a in k["attempts"]:
            for pid, (ok, _plan) in a["parts"].items():
                produced[int(pid)] = produced.get(int(pid), 0) + ok
        if k["lane"] != "done":
            for x in k["parts"]:
                pipeline[x["part_id"]] = pipeline.get(x["part_id"], 0) + x["qty"]
    out = []
    for r in c.execute("SELECT * FROM parts WHERE project_id=? ORDER BY position, id", (p["id"],)):
        need = (r["per_set"] or 0) * sets
        done, pipe = produced.get(r["id"], 0), pipeline.get(r["id"], 0)
        out.append({"id": r["id"], "name": r["name"], "per_set": r["per_set"], "need": need,
                    "produced": done, "pipeline": pipe, "todo": max(0, need - done - pipe)})
    return out


def find_part(c, project_id: int, name: str, create_per_set: Optional[int] = None) -> Optional[int]:
    """Part matching an object name (by name or alias, case-insensitive); created if asked."""
    key = part_name(name).lower()
    for r in c.execute("SELECT id, name, aliases FROM parts WHERE project_id=?", (project_id,)):
        if r["name"].lower() == key or key in (a.lower() for a in json.loads(r["aliases"] or "[]")):
            return r["id"]
    if create_per_set is None:
        return None
    pos = c.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM parts WHERE project_id=?", (project_id,)).fetchone()[0]
    return c.execute("INSERT INTO parts (project_id, name, per_set, position) VALUES (?,?,?,?)",
                     (project_id, part_name(name), create_per_set, pos)).lastrowid


def set_composition(c, card_id: int, comp: Dict[int, int]):
    c.execute("DELETE FROM card_parts WHERE card_id=?", (card_id,))
    for pid, q in comp.items():
        if q > 0:
            c.execute("INSERT INTO card_parts (card_id, part_id, qty) VALUES (?,?,?)", (card_id, int(pid), int(q)))
    # Prints recorded before the card had a composition: a success produced the whole plate.
    full = {int(pid): [int(q), int(q)] for pid, q in comp.items() if q > 0}
    c.execute("UPDATE attempts SET parts=? WHERE card_id=? AND result='success' AND (parts IS NULL OR parts='{}')",
              (json.dumps(full), card_id))
    c.execute("UPDATE attempts SET parts=? WHERE card_id=? AND result='fail' AND (parts IS NULL OR parts='{}')",
              (json.dumps({pid: [0, q] for pid, (_ok, q) in full.items()}), card_id))


def has_composition(c, card_id: int) -> bool:
    return c.execute("SELECT 1 FROM card_parts WHERE card_id=? LIMIT 1", (card_id,)).fetchone() is not None


def composition_from_names(c, project_id: int, names: dict, create_per_set: int = 1) -> Dict[int, int]:
    comp: Dict[int, int] = {}
    for n, q in names.items():
        pid = find_part(c, project_id, n, create_per_set)
        comp[pid] = comp.get(pid, 0) + q
    return comp


def has_parts(c, project_id: int) -> bool:
    return c.execute("SELECT 1 FROM parts WHERE project_id=? LIMIT 1", (project_id,)).fetchone() is not None


def _scale(a: dict, k: int) -> dict:
    """Analysis of one plate run k times."""
    return {**a, "time_s": a["time_s"] * k, "g": a["g"] * k, "cost": a.get("cost", 0) * k,
            "filaments": [{**f, "g": f["g"] * k, "m": f["m"] * k, "cost": f.get("cost", 0) * k}
                          for f in a["filaments"]]}


def project_stats(cards: list) -> Optional[dict]:
    """Bambu Studio style totals for the whole project, counting every plate to print."""
    rows = [c for c in cards if c["analysis"]]
    if not rows:
        return None
    total = merge([_scale(c["analysis"], c["plates"]) for c in rows])
    left = [c for c in rows if c["plates_left"]]
    left_m = merge([_scale(c["analysis"], c["plates_left"]) for c in left]) if left else None
    return {
        "filaments": total["filaments"],
        "time_s": total["time_s"], "g": total["g"], "cost": total["cost"],
        "left_time_s": left_m["time_s"] if left_m else 0,
        "left_g": left_m["g"] if left_m else 0,
        # every analysed card, plus imported plates not sliced yet (time unknown)
        "rows": [{"card_id": c["id"], "title": c["title"], "lane": c["lane"], "plate_index": c["plate_index"],
                  "time_s": c["analysis"]["time_s"] * c["plates"] if c["analysis"] else None,
                  "plates": c["plates"], "plates_done": c["plates_done"]}
                 for c in sorted((c for c in cards if c["analysis"] or c["plate_index"]),
                                 key=lambda c: (c["plate_index"] is None, c["plate_index"] or 0, c["id"]))],
    }


# ---------- board ----------

@app.get("/api/board")
def board():
    with db.conn() as c:
        return {"projects": [project_dict(c, p) for p in c.execute(
            "SELECT * FROM projects ORDER BY position, id")]}


# ---------- projects ----------

class ProjectIn(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    bom: Optional[list] = None
    collapsed: Optional[bool] = None
    sets: Optional[int] = None


@app.post("/api/projects")
def create_project(body: ProjectIn):
    with db.conn() as c:
        pos = c.execute("SELECT COALESCE(MIN(position), 0) - 1 FROM projects").fetchone()[0]
        cur = c.execute("INSERT INTO projects (name, url, position) VALUES (?,?,?)",
                        ((body.name or "Projet").strip(), body.url or "", pos))
        return project_dict(c, c.execute("SELECT * FROM projects WHERE id=?", (cur.lastrowid,)).fetchone())


@app.patch("/api/projects/{pid}")
def update_project(pid: int, body: ProjectIn):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "bom" in fields:
        fields["bom"] = json.dumps(fields["bom"])
    if "collapsed" in fields:
        fields["collapsed"] = int(fields["collapsed"])
    if "sets" in fields:
        fields["sets"] = max(1, fields["sets"])
    with db.conn() as c:
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            c.execute(f"UPDATE projects SET {sets} WHERE id=?", (*fields.values(), pid))
        p = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        if not p:
            raise HTTPException(404)
        return project_dict(c, p)


@app.delete("/api/projects/{pid}")
def delete_project(pid: int):
    with db.conn() as c:
        for f in c.execute("SELECT f.id FROM files f LEFT JOIN cards k ON f.card_id=k.id "
                           "WHERE f.project_id=? OR k.project_id=?", (pid, pid)).fetchall():
            delete_file_row(c, f["id"])
        c.execute("DELETE FROM projects WHERE id=?", (pid,))
    return {"ok": True}


class Order(BaseModel):
    ids: List[int]


@app.post("/api/projects/order")
def order_projects(body: Order):
    with db.conn() as c:
        for i, pid in enumerate(body.ids):
            c.execute("UPDATE projects SET position=? WHERE id=?", (i, pid))
    return {"ok": True}


# ---------- cards ----------

class CardIn(BaseModel):
    project_id: Optional[int] = None
    title: Optional[str] = None
    url: Optional[str] = None
    notes: Optional[str] = None
    qty: Optional[int] = None
    per_plate: Optional[int] = None
    analysis: Optional[dict] = None


def get_card(c, cid):
    r = c.execute("SELECT * FROM cards WHERE id=?", (cid,)).fetchone()
    if not r:
        raise HTTPException(404)
    return r


@app.post("/api/cards")
def create_card(body: CardIn):
    if not body.project_id:
        raise HTTPException(400, "project_id")
    with db.conn() as c:
        pos = c.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM cards WHERE project_id=? AND lane='analysed'",
                        (body.project_id,)).fetchone()[0]
        cur = c.execute("INSERT INTO cards (project_id, title, url, qty, position, lane) VALUES (?,?,?,?,?,'analysed')",
                        (body.project_id, (body.title or "Pièce").strip(), body.url or "", body.qty or 1, pos))
        return card_dict(c, get_card(c, cur.lastrowid))


@app.patch("/api/cards/{cid}")
def update_card(cid: int, body: CardIn):
    fields = {k: v for k, v in body.model_dump().items() if v is not None and k != "project_id"}
    if "analysis" in fields:  # {} clears it (e.g. a hand-entered time erased)
        fields["analysis"] = json.dumps(fields["analysis"]) if fields["analysis"] else None
    with db.conn() as c:
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            c.execute(f"UPDATE cards SET {sets} WHERE id=?", (*fields.values(), cid))
        return card_dict(c, get_card(c, cid))


@app.delete("/api/cards/{cid}")
def delete_card(cid: int):
    with db.conn() as c:
        for f in c.execute("SELECT id FROM files WHERE card_id=?", (cid,)).fetchall():
            delete_file_row(c, f["id"])
        c.execute("DELETE FROM cards WHERE id=?", (cid,))
    return {"ok": True}


class Move(BaseModel):
    card_id: int
    project_id: int
    lane: str
    ids: List[int]                 # final order of the target lane
    result: Optional[str] = None   # required when entering "done"
    note: Optional[str] = ""
    change: Optional[str] = None   # what changed, when leaving "done" for a retry
    parts: Optional[Dict[int, int]] = None  # pieces that came out good, per part (entering "done")


@app.post("/api/cards/move")
def move_card(body: Move):
    if body.lane == "park":
        body.lane = "analysed"
    if body.lane not in LANES:
        raise HTTPException(400, "lane")
    with db.conn() as c:
        card = get_card(c, body.card_id)
        if body.lane == "done" and card["lane"] != "done":
            if body.result not in ("success", "fail"):
                raise HTTPException(400, "result")
            plan = {r["part_id"]: r["qty"] for r in c.execute(
                "SELECT part_id, qty FROM card_parts WHERE card_id=?", (card["id"],))}
            given = body.parts or {}
            res = {}
            for pid, q in plan.items():
                ok = given.get(pid, q if body.result == "success" else 0)
                res[pid] = [max(0, min(q, int(ok))), q]
            c.execute("INSERT INTO attempts (card_id, result, note, change, parts) VALUES (?,?,?,?,?)",
                      (card["id"], body.result, body.note or "", card["pending_change"] or "", json.dumps(res)))
            c.execute("UPDATE cards SET pending_change='' WHERE id=?", (card["id"],))
        if card["lane"] == "done" and body.lane != "done":
            if body.change is not None:
                c.execute("UPDATE cards SET pending_change=? WHERE id=?", (body.change, card["id"]))
            # Retry after a partial print: only the pieces that are still missing.
            last = c.execute("SELECT parts FROM attempts WHERE card_id=? ORDER BY id DESC LIMIT 1",
                             (card["id"],)).fetchone()
            if last:
                done = json.loads(last["parts"] or "{}")
                missing = {int(pid): plan - ok for pid, (ok, plan) in done.items()}
                if 0 < sum(missing.values()) < sum(plan for _ok, plan in done.values()):
                    set_composition(c, card["id"], missing)
        c.execute("UPDATE cards SET lane=?, project_id=? WHERE id=?",
                  (body.lane, body.project_id, card["id"]))
        for i, cid in enumerate(body.ids):
            c.execute("UPDATE cards SET position=? WHERE id=?", (i, cid))
        return card_dict(c, get_card(c, card["id"]))


class Composition(BaseModel):
    parts: Dict[int, int]


@app.put("/api/cards/{cid}/parts")
def put_card_parts(cid: int, body: Composition):
    with db.conn() as c:
        get_card(c, cid)
        set_composition(c, cid, body.parts)
        return card_dict(c, get_card(c, cid))


def _copy_file(c, fid: int, card_id: int) -> int:
    r = c.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
    stored = uuid.uuid4().hex + os.path.splitext(r["stored"])[1]
    shutil.copyfile(os.path.join(db.FILES_DIR, r["stored"]), os.path.join(db.FILES_DIR, stored))
    return c.execute("INSERT INTO files (card_id, name, kind, stored, size) VALUES (?,?,?,?,?)",
                     (card_id, r["name"], r["kind"], stored, r["size"])).lastrowid


@app.post("/api/cards/{cid}/duplicate")
def duplicate_card(cid: int):
    """Same print again: new card with the same files, slicing data and composition."""
    with db.conn() as c:
        k = get_card(c, cid)
        lane = "analysed"
        pos = c.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM cards WHERE project_id=? AND lane=?",
                        (k["project_id"], lane)).fetchone()[0]
        new = c.execute(
            "INSERT INTO cards (project_id, title, lane, position, qty, per_plate, url, notes, analysis, plate_index) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (k["project_id"], k["title"], lane, pos, k["qty"], k["per_plate"], k["url"], k["notes"],
             k["analysis"], k["plate_index"])).lastrowid
        for f in c.execute("SELECT id FROM files WHERE card_id=?", (cid,)).fetchall():
            nf = _copy_file(c, f["id"], new)
            if f["id"] == k["preview_file_id"]:
                c.execute("UPDATE cards SET preview_file_id=? WHERE id=?", (nf, new))
        c.execute("INSERT INTO card_parts (card_id, part_id, qty) SELECT ?, part_id, qty FROM card_parts WHERE card_id=?",
                  (new, cid))
        return card_dict(c, get_card(c, new))


class PartIn(BaseModel):
    project_id: Optional[int] = None
    name: Optional[str] = None
    per_set: Optional[int] = None


@app.post("/api/parts")
def create_part(body: PartIn):
    with db.conn() as c:
        pid = find_part(c, body.project_id, body.name or "Pièce", body.per_set if body.per_set is not None else 1)
        return {"id": pid}


@app.patch("/api/parts/{pid}")
def update_part(pid: int, body: PartIn):
    with db.conn() as c:
        r = c.execute("SELECT * FROM parts WHERE id=?", (pid,)).fetchone()
        if not r:
            raise HTTPException(404)
        if body.name and body.name.strip() and body.name.strip() != r["name"]:
            # keep the old name so object names from Bambu Studio still map here
            aliases = json.loads(r["aliases"] or "[]")
            if r["name"] not in aliases:
                aliases.append(r["name"])
            c.execute("UPDATE parts SET name=?, aliases=? WHERE id=?", (body.name.strip(), json.dumps(aliases), pid))
        if body.per_set is not None:
            c.execute("UPDATE parts SET per_set=? WHERE id=?", (max(0, body.per_set), pid))
    return {"ok": True}


@app.delete("/api/parts/{pid}")
def delete_part(pid: int):
    with db.conn() as c:
        c.execute("DELETE FROM parts WHERE id=?", (pid,))
    return {"ok": True}


@app.delete("/api/attempts/{aid}")
def delete_attempt(aid: int):
    with db.conn() as c:
        c.execute("DELETE FROM attempts WHERE id=?", (aid,))
    return {"ok": True}


# ---------- files ----------

@app.post("/api/upload")
async def upload(file: UploadFile = File(...), project_id: Optional[int] = Form(None),
                 card_id: Optional[int] = Form(None), role: str = Form("attach")):
    """role: attach (card file), preview (card image), image (project image), bambu (project file),
    slicing (a .gcode.3mf of all plates: fills time/filament of the project's plate cards, not kept)."""
    data = await file.read()
    name = safe_name(file.filename)
    if role == "slicing" and project_id:
        return import_slicing(project_id, data)
    fid = store_bytes(data, name, project_id if role in ("image", "bambu") else None,
                      card_id if role in ("attach", "preview") else None)
    with db.conn() as c:
        if role == "image" and project_id:
            old = c.execute("SELECT image_file_id FROM projects WHERE id=?", (project_id,)).fetchone()
            c.execute("UPDATE projects SET image_file_id=? WHERE id=?", (fid, project_id))
            if old and old[0]:
                delete_file_row(c, old[0])
        elif role == "bambu" and project_id:
            old = c.execute("SELECT bambu_file_id, image_file_id FROM projects WHERE id=?", (project_id,)).fetchone()
            c.execute("UPDATE projects SET bambu_file_id=? WHERE id=?", (fid, project_id))
            if old and old[0]:
                delete_file_row(c, old[0])
        elif role == "preview" and card_id:
            old = c.execute("SELECT preview_file_id FROM cards WHERE id=?", (card_id,)).fetchone()
            c.execute("UPDATE cards SET preview_file_id=? WHERE id=?", (fid, card_id))
            if old and old[0]:
                delete_file_row(c, old[0])

    # Pull thumbnail / slicing data out of 3MF files.
    kind = kind_of(name)
    if kind in ("3mf", "gcode3mf"):
        info = parse_3mf(os.path.join(db.FILES_DIR, _stored(fid)))
        if role == "attach" and card_id:
            with db.conn() as c:
                card = get_card(c, card_id)
            if info["thumbnail"] and not card["preview_file_id"]:
                tid = store_bytes(info["thumbnail"], "preview.png", card_id=card_id)
                with db.conn() as c:
                    c.execute("UPDATE cards SET preview_file_id=? WHERE id=?", (tid, card_id))
            if info["analysis"] and info["analysis"].get("parts"):
                with db.conn() as c:
                    if has_parts(c, card["project_id"]):
                        set_composition(c, card_id, composition_from_names(
                            c, card["project_id"], info["analysis"]["parts"]))
            if info["analysis"]:
                with db.conn() as c:
                    c.execute("UPDATE cards SET analysis=? WHERE id=?", (json.dumps(info["analysis"]), card_id))
                    # Several objects on the plate: assume each is one copy, unless already set by hand.
                    n = info["analysis"].get("objects", 0)
                    if n > 1 and (card["per_plate"] or 1) == 1:
                        c.execute("UPDATE cards SET per_plate=? WHERE id=?", (n, card_id))
                    if card["lane"] == "park":
                        pos = c.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM cards "
                                        "WHERE project_id=? AND lane='analysed'", (card["project_id"],)).fetchone()[0]
                        c.execute("UPDATE cards SET lane='analysed', position=? WHERE id=?", (pos, card_id))
        if role == "bambu" and project_id:
            if info["parts"]:
                with db.conn() as c:  # the project's objects become its parts (existing ones kept as set)
                    for n, q in info["parts"].items():
                        find_part(c, project_id, n, q)
            import_plates(project_id, info["plates"])
        if role == "bambu" and project_id and info["thumbnail"]:
            with db.conn() as c:
                has = c.execute("SELECT image_file_id FROM projects WHERE id=?", (project_id,)).fetchone()[0]
            if not has:
                tid = store_bytes(info["thumbnail"], "image.png", project_id=project_id)
                with db.conn() as c:
                    c.execute("UPDATE projects SET image_file_id=? WHERE id=?", (tid, project_id))
    with db.conn() as c:
        return file_row(c.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone())


def import_slicing(project_id: int, data: bytes) -> dict:
    """Bambu Studio does not keep slicing results in a saved project. "Export all plate sliced
    file" does: read it and give each plate card of the project its time, grams and filaments."""
    tmp = os.path.join(db.FILES_DIR, uuid.uuid4().hex + ".3mf")
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        info = parse_3mf(tmp)
    finally:
        os.remove(tmp)
    sliced = [p for p in info["plates"] if p["analysis"]]
    if not sliced:
        raise HTTPException(422, "Aucun plateau tranché dans ce fichier (il faut un .gcode.3mf exporté).")
    updated = 0
    with db.conn() as c:
        has_plate_cards = c.execute("SELECT 1 FROM cards WHERE project_id=? AND plate_index IS NOT NULL LIMIT 1",
                                    (project_id,)).fetchone()
    if not has_plate_cards:  # no project imported yet: the plates become the cards
        import_plates(project_id, info["plates"])
        return {"plates": len(sliced), "updated": len(sliced)}
    for pl in sliced:
        with db.conn() as c:
            n = c.execute("UPDATE cards SET analysis=? WHERE project_id=? AND plate_index=?",
                          (json.dumps(pl["analysis"]), project_id, pl["index"])).rowcount
        updated += n
    return {"plates": len(sliced), "updated": updated}


def import_plates(project_id: int, plates: list):
    """One card per plate of the project's Bambu file. Re-importing updates the same cards."""
    for pl in plates:
        idx, a = pl["index"], pl["analysis"]
        auto = pl["name"] or f"Plateau {idx}"
        with db.conn() as c:
            same = c.execute("SELECT * FROM cards WHERE project_id=? AND plate_index=? ORDER BY id",
                             (project_id, idx)).fetchall()
            comp = composition_from_names(c, project_id, pl["parts"]) if pl.get("parts") else {}
            for extra in same[1:]:  # duplicates of this plate: refresh slicing data and composition
                if a:
                    c.execute("UPDATE cards SET analysis=? WHERE id=?", (json.dumps(a), extra["id"]))
                if comp and (extra["lane"] != "done" or not has_composition(c, extra["id"])):
                    set_composition(c, extra["id"], comp)
            card = same[0] if same else None
            if card is None:
                n = 1 if comp else max(1, pl["objects"] or 1)
                lane = "analysed"
                pos = c.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM cards WHERE project_id=? AND lane=?",
                                (project_id, lane)).fetchone()[0]
                cid = c.execute(
                    "INSERT INTO cards (project_id, title, lane, position, qty, per_plate, plate_index, analysis) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (project_id, auto, lane, pos, n, n, idx, json.dumps(a) if a else None)).lastrowid
                set_composition(c, cid, comp)
                old_preview = None
            else:
                cid, old_preview = card["id"], card["preview_file_id"]
                if a:
                    c.execute("UPDATE cards SET analysis=? WHERE id=?", (json.dumps(a), cid))
                    if card["lane"] == "park":
                        c.execute("UPDATE cards SET lane='analysed' WHERE id=?", (cid,))
                if pl["name"] and card["title"].startswith("Plateau "):
                    c.execute("UPDATE cards SET title=? WHERE id=?", (pl["name"], cid))
                # done cards keep what they printed, unless they predate parts (then they count as printed)
                if comp and (card["lane"] != "done" or not has_composition(c, cid)):
                    set_composition(c, cid, comp)
        if pl["thumbnail"]:
            tid = store_bytes(pl["thumbnail"], "preview.png", card_id=cid)
            with db.conn() as c:
                c.execute("UPDATE cards SET preview_file_id=? WHERE id=?", (tid, cid))
                if old_preview:
                    delete_file_row(c, old_preview)


def _stored(fid: int) -> str:
    with db.conn() as c:
        r = c.execute("SELECT stored FROM files WHERE id=?", (fid,)).fetchone()
    if not r:
        raise HTTPException(404)
    return r["stored"]


@app.delete("/api/files/{fid}")
def delete_file(fid: int):
    with db.conn() as c:
        c.execute("UPDATE projects SET bambu_file_id=NULL WHERE bambu_file_id=?", (fid,))
        c.execute("UPDATE projects SET image_file_id=NULL WHERE image_file_id=?", (fid,))
        c.execute("UPDATE cards SET preview_file_id=NULL WHERE preview_file_id=?", (fid,))
        delete_file_row(c, fid)
    return {"ok": True}


@app.get("/files/{fid}/{name}")
def get_file(fid: int, name: str, dl: int = 0):
    with db.conn() as c:
        r = c.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
    if not r:
        raise HTTPException(404)
    path = os.path.join(db.FILES_DIR, r["stored"])
    if r["kind"] == "image" and not dl:
        return FileResponse(path)
    return FileResponse(path, filename=r["name"])


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
