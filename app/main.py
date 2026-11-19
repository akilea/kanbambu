import json
import math
import os
import re
import uuid
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .threemf import merge, parse_3mf

LANES = ("park", "analysed", "printing", "done")
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
    attempts = [dict(a) for a in c.execute(
        "SELECT * FROM attempts WHERE card_id=? ORDER BY id", (r["id"],))]
    ok = sum(a["result"] == "success" for a in attempts)
    per = max(1, r["per_plate"] or 1)
    total = max(1, math.ceil((r["qty"] or 1) / per))
    return {
        "id": r["id"], "project_id": r["project_id"], "title": r["title"], "lane": r["lane"],
        "qty": r["qty"], "per_plate": per, "url": r["url"], "notes": r["notes"],
        "plates": total, "plates_done": min(ok, total), "plates_left": max(0, total - ok),
        "plate_index": r["plate_index"],
        "preview": f"/files/{r['preview_file_id']}/preview" if r["preview_file_id"] else None,
        "preview_file_id": r["preview_file_id"],
        "analysis": json.loads(r["analysis"]) if r["analysis"] else None,
        "pending_change": r["pending_change"],
        "files": files, "attempts": attempts,
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
        "image": f"/files/{p['image_file_id']}/image" if p["image_file_id"] else None,
        "bambu": file_row(bambu) if bambu else None,
        "bom": json.loads(p["bom"] or "[]"),
        "cards": cards,
        "stats": project_stats(cards),
    }


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
        pos = c.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM cards WHERE project_id=? AND lane='park'",
                        (body.project_id,)).fetchone()[0]
        cur = c.execute("INSERT INTO cards (project_id, title, url, qty, position) VALUES (?,?,?,?,?)",
                        (body.project_id, (body.title or "Pièce").strip(), body.url or "", body.qty or 1, pos))
        return card_dict(c, get_card(c, cur.lastrowid))


@app.patch("/api/cards/{cid}")
def update_card(cid: int, body: CardIn):
    fields = {k: v for k, v in body.model_dump().items() if v is not None and k != "project_id"}
    if "analysis" in fields:
        fields["analysis"] = json.dumps(fields["analysis"])
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


@app.post("/api/cards/move")
def move_card(body: Move):
    if body.lane not in LANES:
        raise HTTPException(400, "lane")
    with db.conn() as c:
        card = get_card(c, body.card_id)
        if body.lane == "done" and card["lane"] != "done":
            if body.result not in ("success", "fail"):
                raise HTTPException(400, "result")
            c.execute("INSERT INTO attempts (card_id, result, note, change) VALUES (?,?,?,?)",
                      (card["id"], body.result, body.note or "", card["pending_change"] or ""))
            c.execute("UPDATE cards SET pending_change='' WHERE id=?", (card["id"],))
        if card["lane"] == "done" and body.lane != "done" and body.change is not None:
            c.execute("UPDATE cards SET pending_change=? WHERE id=?", (body.change, card["id"]))
        c.execute("UPDATE cards SET lane=?, project_id=? WHERE id=?",
                  (body.lane, body.project_id, card["id"]))
        for i, cid in enumerate(body.ids):
            c.execute("UPDATE cards SET position=? WHERE id=?", (i, cid))
        return card_dict(c, get_card(c, card["id"]))


@app.delete("/api/attempts/{aid}")
def delete_attempt(aid: int):
    with db.conn() as c:
        c.execute("DELETE FROM attempts WHERE id=?", (aid,))
    return {"ok": True}


# ---------- files ----------

@app.post("/api/upload")
async def upload(file: UploadFile = File(...), project_id: Optional[int] = Form(None),
                 card_id: Optional[int] = Form(None), role: str = Form("attach")):
    """role: attach (card file), preview (card image), image (project image), bambu (project file)."""
    data = await file.read()
    name = safe_name(file.filename)
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


def import_plates(project_id: int, plates: list):
    """One card per plate of the project's Bambu file. Re-importing updates the same cards."""
    for pl in plates:
        idx, a = pl["index"], pl["analysis"]
        auto = pl["name"] or f"Plateau {idx}"
        with db.conn() as c:
            card = c.execute("SELECT * FROM cards WHERE project_id=? AND plate_index=?",
                             (project_id, idx)).fetchone()
            if card is None:
                n = max(1, pl["objects"] or 1)
                lane = "analysed" if a else "park"
                pos = c.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM cards WHERE project_id=? AND lane=?",
                                (project_id, lane)).fetchone()[0]
                cid = c.execute(
                    "INSERT INTO cards (project_id, title, lane, position, qty, per_plate, plate_index, analysis) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (project_id, auto, lane, pos, n, n, idx, json.dumps(a) if a else None)).lastrowid
                old_preview = None
            else:
                cid, old_preview = card["id"], card["preview_file_id"]
                if a:
                    c.execute("UPDATE cards SET analysis=? WHERE id=?", (json.dumps(a), cid))
                    if card["lane"] == "park":
                        c.execute("UPDATE cards SET lane='analysed' WHERE id=?", (cid,))
                if pl["name"] and card["title"].startswith("Plateau "):
                    c.execute("UPDATE cards SET title=? WHERE id=?", (pl["name"], cid))
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
