"""Run: KANBAMBU_DATA=$(mktemp -d) python -m pytest tests"""
import io
import json
import os
import struct
import tempfile
import zipfile
import zlib

os.environ.setdefault("KANBAMBU_DATA", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.threemf import parse_3mf  # noqa: E402

SLICE_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <header><header_item key="X-BBL-Client-Type" value="slicer"/></header>
  <plate>
    <metadata key="index" value="1"/>
    <metadata key="prediction" value="5400"/>
    <metadata key="weight" value="42.50"/>
    <object identify_id="1" name="a" skipped="false"/>
    <object identify_id="2" name="b" skipped="false"/>
    <object identify_id="3" name="c" skipped="true"/>
    <filament id="1" type="PLA" color="#FFFFFF" used_m="10.20" used_g="30.40"/>
    <filament id="3" type="PETG" color="#FF0000" used_m="4.00" used_g="12.10"/>
  </plate>
  <plate>
    <metadata key="index" value="2"/>
    <metadata key="prediction" value="600"/>
    <metadata key="weight" value="5.00"/>
    <filament id="1" type="PLA" color="#ffffff" used_m="1.70" used_g="5.00"/>
  </plate>
</config>"""


def png(w=4, h=4, rgb=(34, 160, 107)) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))  # noqa: E731
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_3mf(sliced=True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
        z.writestr("Metadata/plate_1.png", png())
        z.writestr("Metadata/project_settings.config",
                   json.dumps({"filament_settings_id": ["Bambu PLA Basic", "x", "Generic PETG"]}))
        if sliced:
            z.writestr("Metadata/slice_info.config", SLICE_INFO)
    return buf.getvalue()


MODEL_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate><metadata key="plater_id" value="1"/><metadata key="plater_name" value="Cadre"/>
    <model_instance><metadata key="object_id" value="2"/></model_instance></plate>
  <plate><metadata key="plater_id" value="2"/><metadata key="plater_name" value=""/>
    <model_instance><metadata key="object_id" value="3"/></model_instance>
    <model_instance><metadata key="object_id" value="4"/></model_instance></plate>
  <plate><metadata key="plater_id" value="3"/><metadata key="plater_name" value="Roues"/>
    <model_instance><metadata key="object_id" value="5"/></model_instance></plate>
</config>"""

PROJECT_SLICE = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate><metadata key="index" value="1"/><metadata key="prediction" value="19200"/><metadata key="weight" value="300"/>
    <object identify_id="1" name="cadre" skipped="false"/>
    <filament id="1" type="PLA" color="#808080" used_m="100" used_g="300"/></plate>
  <plate><metadata key="index" value="2"/><metadata key="prediction" value="6780"/><metadata key="weight" value="120"/>
    <object identify_id="2" name="a" skipped="false"/><object identify_id="3" name="b" skipped="false"/>
    <filament id="1" type="PLA" color="#808080" used_m="20" used_g="60"/>
    <filament id="3" type="PETG" color="#5A7A9A" used_m="20" used_g="60"/></plate>
</config>"""


def make_project() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
        for i in (1, 2, 3):
            z.writestr(f"Metadata/plate_{i}.png", png(4, 4, (i * 60, 90, 120)))
        z.writestr("Metadata/model_settings.config", MODEL_SETTINGS)
        z.writestr("Metadata/slice_info.config", PROJECT_SLICE)
        z.writestr("Metadata/project_settings.config", json.dumps({
            "filament_settings_id": ["Bambu PLA Basic", "x", "Bambu PETG HF"],
            "filament_cost": ["20", "0", "30"]}))
    return buf.getvalue()


def test_parse(tmp_path):
    p = tmp_path / "a.gcode.3mf"
    p.write_bytes(make_3mf())
    info = parse_3mf(str(p))
    a = info["analysis"]
    assert info["thumbnail"].startswith(b"\x89PNG")
    assert a["time_s"] == 6000 and a["g"] == 47.5 and len(a["plates"]) == 2
    white = next(f for f in a["filaments"] if f["type"] == "PLA")
    assert white["g"] == 35.4 and white["profile"] == "Bambu PLA Basic"
    red = next(f for f in a["filaments"] if f["type"] == "PETG")
    assert red["profile"] == "Generic PETG"
    assert a["objects"] == 2


def test_plates():
    c = TestClient(app)
    p = c.post("/api/projects", json={"name": "Mur"}).json()
    card = c.post("/api/cards", json={"project_id": p["id"], "title": "Crochet"}).json()
    assert (card["plates"], card["plates_left"]) == (1, 1)
    card = c.patch(f"/api/cards/{card['id']}", json={"qty": 10}).json()
    # sliced plate with 2 objects -> 2 copies per plate, 5 plates
    c.post("/api/upload", data={"card_id": card["id"], "role": "attach"},
           files={"file": ("crochet.gcode.3mf", make_3mf(True))})
    card = c.get("/api/board").json()["projects"][0]["cards"][0]
    assert (card["per_plate"], card["plates"], card["plates_left"]) == (2, 5, 5)
    mv = lambda lane, **kw: c.post("/api/cards/move", json={  # noqa: E731
        "card_id": card["id"], "project_id": p["id"], "lane": lane, "ids": [card["id"]], **kw}).json()
    mv("printing"); out = mv("done", result="success")
    assert (out["plates_done"], out["plates_left"]) == (1, 4)
    mv("printing"); out = mv("done", result="fail", note="Décollé")      # a fail is not a plate
    assert out["plates_left"] == 4
    c.patch(f"/api/cards/{card['id']}", json={"per_plate": 5})            # 2 plates total
    mv("printing", change="Colle"); out = mv("done", result="success")
    assert (out["plates"], out["plates_done"], out["plates_left"]) == (2, 2, 0)
    c.delete(f"/api/projects/{p['id']}")


def test_flow():
    c = TestClient(app)
    p = c.post("/api/projects", json={"name": "Lampe"}).json()
    card = c.post("/api/cards", json={"project_id": p["id"], "title": "Base"}).json()
    assert card["lane"] == "analysed"

    # plain project 3MF: thumbnail only, no slicing data
    r = c.post("/api/upload", data={"card_id": card["id"], "role": "attach"},
               files={"file": ("base.3mf", make_3mf(False))})
    assert r.status_code == 200
    card = next(k for k in c.get("/api/board").json()["projects"][0]["cards"])
    assert card["lane"] == "analysed" and card["preview"] and card["analysis"] is None
    assert [f["name"] for f in card["files"]] == ["base.3mf"]

    # sliced plate: analysis + auto move to Analysed
    c.post("/api/upload", data={"card_id": card["id"], "role": "attach"},
           files={"file": ("base.gcode.3mf", make_3mf(True))})
    card = c.get("/api/board").json()["projects"][0]["cards"][0]
    assert card["lane"] == "analysed" and card["analysis"]["g"] == 47.5

    mv = lambda lane, **kw: c.post("/api/cards/move", json={  # noqa: E731
        "card_id": card["id"], "project_id": p["id"], "lane": lane, "ids": [card["id"]], **kw})
    assert mv("printing").status_code == 200
    assert mv("done").status_code == 400                      # result required
    assert mv("done", result="fail", note="Warping").json()["ko"] == 1
    assert mv("printing", change="Brim 5 mm").json()["pending_change"] == "Brim 5 mm"
    out = mv("done", result="success", note="Ajouter un chanfrein").json()
    assert (out["ok"], out["ko"]) == (1, 1)
    assert out["attempts"][1]["change"] == "Brim 5 mm" and out["pending_change"] == ""

    # project bits
    c.patch(f"/api/projects/{p['id']}", json={"bom": [{"qty": 4, "name": "Vis M3", "done": False}],
                                               "url": "https://makerworld.com/x"})
    c.post("/api/upload", data={"project_id": p["id"], "role": "bambu"},
           files={"file": ("lampe.3mf", make_3mf(False))})
    proj = c.get("/api/board").json()["projects"][0]
    assert proj["bom"][0]["qty"] == 4 and proj["bambu"]["name"] == "lampe.3mf" and proj["image"]
    assert c.get(proj["bambu"]["url"]).status_code == 200

    assert c.delete(f"/api/projects/{p['id']}").json()["ok"]
    assert c.get("/api/board").json()["projects"] == []


def test_project_plates(tmp_path):
    f = tmp_path / "robot.3mf"
    f.write_bytes(make_project())
    info = parse_3mf(str(f))
    assert [p["index"] for p in info["plates"]] == [1, 2, 3]
    p1, p2, p3 = info["plates"]
    assert p1["name"] == "Cadre" and p1["analysis"]["time_s"] == 19200 and p1["thumbnail"]
    assert p2["objects"] == 2 and p2["analysis"]["cost"] == 3.0   # 60 g @ 20 + 60 g @ 30
    assert p3["analysis"] is None and p3["objects"] == 1          # not sliced yet

    c = TestClient(app)
    proj = c.post("/api/projects", json={"name": "Robot"}).json()
    c.post("/api/upload", data={"project_id": proj["id"], "role": "bambu"}, files={"file": ("robot.3mf", make_project())})
    pr = next(p for p in c.get("/api/board").json()["projects"] if p["id"] == proj["id"])
    cards = {k["plate_index"]: k for k in pr["cards"]}
    assert cards[1]["title"] == "Cadre" and cards[1]["lane"] == "analysed" and cards[1]["preview"]
    assert cards[2]["title"] == "Plateau 2" and cards[2]["plates"] == 1
    assert {x["name"]: x["qty"] for x in cards[2]["parts"]} == {"a": 1, "b": 1}
    assert cards[3]["lane"] == "analysed" and cards[3]["analysis"] is None
    st = pr["stats"]
    assert st["time_s"] == 19200 + 6780 and st["g"] == 420 and st["cost"] == 9.0
    assert {f["profile"]: f["g"] for f in st["filaments"]} == {"Bambu PLA Basic": 360, "Bambu PETG HF": 60}

    # print plate 1, then re-import: same cards, nothing duplicated
    c.post("/api/cards/move", json={"card_id": cards[1]["id"], "project_id": proj["id"], "lane": "done",
                                    "ids": [cards[1]["id"]], "result": "success"})
    c.post("/api/upload", data={"project_id": proj["id"], "role": "bambu"}, files={"file": ("robot.3mf", make_project())})
    pr = next(p for p in c.get("/api/board").json()["projects"] if p["id"] == proj["id"])
    assert len(pr["cards"]) == 3
    assert pr["stats"]["left_time_s"] == 6780
    c.delete(f"/api/projects/{proj['id']}")


AMS_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <object id="2"><metadata key="name" value="Base.stl"/></object>
  <object id="3"><metadata key="name" value="Bras"/></object>
  <object id="4"><metadata key="name" value="Clip"/></object>
  <plate><metadata key="plater_id" value="1"/><metadata key="plater_name" value="Base"/>
    <model_instance><metadata key="object_id" value="2"/><metadata key="instance_id" value="0"/></model_instance></plate>
  <plate><metadata key="plater_id" value="2"/><metadata key="plater_name" value=""/>
    <model_instance><metadata key="object_id" value="3"/><metadata key="instance_id" value="0"/></model_instance>
    <model_instance><metadata key="object_id" value="3"/><metadata key="instance_id" value="1"/></model_instance>
    <model_instance><metadata key="object_id" value="4"/><metadata key="instance_id" value="0"/></model_instance></plate>
</config>"""

AMS_SLICE = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate><metadata key="index" value="1"/><metadata key="prediction" value="3600"/><metadata key="weight" value="50"/>
    <object identify_id="1" name="Base.stl" skipped="false"/>
    <filament id="1" type="PLA" color="#000000" used_m="16" used_g="50"/></plate>
</config>"""

MIXED_SLICE = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate><metadata key="index" value="1"/><metadata key="prediction" value="7200"/><metadata key="weight" value="80"/>
    <object identify_id="1" name="Bras" skipped="false"/>
    <object identify_id="2" name="Bras" skipped="false"/>
    <object identify_id="3" name="Bras" skipped="false"/>
    <object identify_id="4" name="Base" skipped="false"/>
    <object identify_id="5" name="Clip" skipped="true"/>
    <filament id="1" type="PLA" color="#000000" used_m="26" used_g="80"/></plate>
</config>"""


def _zip(**files) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
        z.writestr("Metadata/plate_1.png", png())
        for k, v in files.items():
            z.writestr(f"Metadata/{k}.config", v)
    return buf.getvalue()


def test_parts_inventory():
    c = TestClient(app)
    proj = c.post("/api/projects", json={"name": "AMS holder"}).json()
    pid = proj["id"]
    get = lambda: next(p for p in c.get("/api/board").json()["projects"] if p["id"] == pid)  # noqa: E731
    inv = lambda: {x["name"]: (x["need"], x["produced"], x["pipeline"], x["todo"]) for x in get()["parts"]}  # noqa: E731

    c.post("/api/upload", data={"project_id": pid, "role": "bambu"},
           files={"file": ("ams.3mf", _zip(model_settings=AMS_SETTINGS, slice_info=AMS_SLICE))})
    c.patch(f"/api/projects/{pid}", json={"sets": 2})
    # one copy of the project = 1 Base, 2 Bras, 1 Clip; two copies wanted
    assert inv() == {"Base": (2, 0, 1, 1), "Bras": (4, 0, 2, 2), "Clip": (2, 0, 1, 1)}
    cards = {k["plate_index"]: k for k in get()["cards"]}
    assert {x["name"]: x["qty"] for x in cards[2]["parts"]} == {"Bras": 2, "Clip": 1}
    assert cards[1]["lane"] == "analysed" and cards[2]["lane"] == "analysed"

    # second copy of plate 1
    dup = c.post(f"/api/cards/{cards[1]['id']}/duplicate").json()
    assert dup["lane"] == "analysed" and dup["parts"][0]["qty"] == 1
    assert inv()["Base"] == (2, 0, 2, 0)

    # a mixed plate sliced by hand: 3 Bras + 1 Base (Clip skipped)
    mixed = c.post("/api/cards", json={"project_id": pid, "title": "Mix"}).json()
    c.post("/api/upload", data={"card_id": mixed["id"], "role": "attach"},
           files={"file": ("mix.gcode.3mf", _zip(slice_info=MIXED_SLICE))})
    m = next(k for k in get()["cards"] if k["id"] == mixed["id"])
    comp = {x["name"]: x["qty"] for x in m["parts"]}
    assert comp == {"Bras": 3, "Base": 1} and m["lane"] == "analysed"
    ids = {x["name"]: x["part_id"] for x in m["parts"]}

    mv = lambda card, lane, **kw: c.post("/api/cards/move", json={  # noqa: E731
        "card_id": card, "project_id": pid, "lane": lane, "ids": [card], **kw}).json()
    # partial: 2 of 3 Bras came out fine
    mv(m["id"], "printing")
    out = mv(m["id"], "done", result="success", parts={ids["Bras"]: 2})
    assert out["attempts"][-1]["partial"]
    assert inv()["Bras"] == (4, 2, 2, 0) and inv()["Base"] == (2, 1, 2, 0)
    # retry only what is missing
    out = mv(m["id"], "park", change="Brim")
    assert {x["name"]: x["qty"] for x in out["parts"]} == {"Bras": 1}
    assert inv()["Bras"] == (4, 2, 3, 0)
    # a failed print produces nothing
    mv(m["id"], "printing")
    out = mv(m["id"], "done", result="fail", note="Spaghetti")
    assert inv()["Bras"] == (4, 2, 2, 0)

    # renaming a part keeps the old object name as an alias
    c.patch(f"/api/parts/{ids['Bras']}", json={"name": "Bras AMS"})
    k = c.post("/api/cards", json={"project_id": pid, "title": "x"}).json()
    c.post("/api/upload", data={"card_id": k["id"], "role": "attach"},
           files={"file": ("x.gcode.3mf", _zip(slice_info=MIXED_SLICE))})
    assert {x["name"] for x in next(q for q in get()["cards"] if q["id"] == k["id"])["parts"]} == {"Bras AMS", "Base"}
    c.delete(f"/api/projects/{pid}")


def test_parts_added_after_printing():
    """Plates printed before the project had parts count as produced once the .3mf is re-imported."""
    c = TestClient(app)
    pid = c.post("/api/projects", json={"name": "Ancien"}).json()["id"]
    # old-style import: no object names yet -> no parts
    c.post("/api/upload", data={"project_id": pid, "role": "bambu"},
           files={"file": ("p.3mf", _zip(slice_info=AMS_SLICE))})
    card = c.get("/api/board").json()["projects"]
    card = next(p for p in card if p["id"] == pid)["cards"][0]
    with __import__("app.db", fromlist=["conn"]).conn() as cx:   # simulate a pre-parts database
        cx.execute("DELETE FROM card_parts"); cx.execute("DELETE FROM parts WHERE project_id=?", (pid,))
    c.post("/api/cards/move", json={"card_id": card["id"], "project_id": pid, "lane": "done",
                                    "ids": [card["id"]], "result": "success"})
    c.patch(f"/api/projects/{pid}", json={"sets": 2})
    c.post("/api/upload", data={"project_id": pid, "role": "bambu"},
           files={"file": ("p.3mf", _zip(model_settings=AMS_SETTINGS, slice_info=AMS_SLICE))})
    pr = next(p for p in c.get("/api/board").json()["projects"] if p["id"] == pid)
    inv = {x["name"]: (x["need"], x["produced"], x["todo"]) for x in pr["parts"]}
    assert inv["Base"] == (2, 1, 1)
    c.delete(f"/api/projects/{pid}")


def test_manual_time():
    c = TestClient(app)
    pid = c.post("/api/projects", json={"name": "Main"}).json()["id"]
    k = c.post("/api/cards", json={"project_id": pid, "title": "Sans slicing"}).json()
    a = {"manual": True, "time_s": 5400, "g": 0, "cost": 0, "plates": [{"index": 1, "time_s": 5400, "g": 0}], "filaments": []}
    assert c.patch(f"/api/cards/{k['id']}", json={"analysis": a}).json()["analysis"]["time_s"] == 5400
    pr = next(p for p in c.get("/api/board").json()["projects"] if p["id"] == pid)
    assert pr["stats"]["time_s"] == 5400
    assert c.patch(f"/api/cards/{k['id']}", json={"analysis": {}}).json()["analysis"] is None
    c.delete(f"/api/projects/{pid}")


SAVED_PROJECT_SLICE = """<?xml version="1.0" encoding="UTF-8"?>
<config><header><header_item key="X-BBL-Client-Type" value="slicer"/></header></config>"""

ALL_PLATES_SLICE = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate><metadata key="index" value="1"/><metadata key="prediction" value="3600"/><metadata key="weight" value="50"/>
    <object identify_id="1" name="Base.stl" skipped="false"/>
    <filament id="1" type="PLA" color="#000000" used_m="16" used_g="50"/></plate>
  <plate><metadata key="index" value="2"/><metadata key="prediction" value="5400"/><metadata key="weight" value="70"/>
    <object identify_id="2" name="Bras" skipped="false"/><object identify_id="3" name="Bras" skipped="false"/>
    <object identify_id="4" name="Clip" skipped="false"/>
    <filament id="1" type="PLA" color="#000000" used_m="23" used_g="70"/></plate>
</config>"""


def test_slicing_from_export():
    """A saved Bambu project has an empty slice_info: times come from 'Export all plate sliced file'."""
    c = TestClient(app)
    pid = c.post("/api/projects", json={"name": "Stacker"}).json()["id"]
    c.post("/api/upload", data={"project_id": pid, "role": "bambu"},
           files={"file": ("p.3mf", _zip(model_settings=AMS_SETTINGS, slice_info=SAVED_PROJECT_SLICE))})
    pr = lambda: next(p for p in c.get("/api/board").json()["projects"] if p["id"] == pid)  # noqa: E731
    assert all(k["analysis"] is None for k in pr()["cards"]) and pr()["stats"] is None
    r = c.post("/api/upload", data={"project_id": pid, "role": "slicing"},
               files={"file": ("p.gcode.3mf", _zip(slice_info=ALL_PLATES_SLICE))}).json()
    assert r == {"plates": 2, "updated": 2}
    times = {k["plate_index"]: k["analysis"]["time_s"] for k in pr()["cards"]}
    assert times == {1: 3600, 2: 5400} and pr()["stats"]["time_s"] == 9000
    assert pr()["bambu"]["name"] == "p.3mf"          # the project file is kept
    bad = c.post("/api/upload", data={"project_id": pid, "role": "slicing"},
                 files={"file": ("p.3mf", _zip(slice_info=SAVED_PROJECT_SLICE))})
    assert bad.status_code == 422
    c.delete(f"/api/projects/{pid}")


def test_requeued_plate_counts_as_remaining():
    """A plate printed once, then put back in the Queue for the next copy, is still to print."""
    c = TestClient(app)
    pid = c.post("/api/projects", json={"name": "Deux fois"}).json()["id"]
    c.post("/api/upload", data={"project_id": pid, "role": "bambu"},
           files={"file": ("p.3mf", _zip(model_settings=AMS_SETTINGS, slice_info=AMS_SLICE))})
    c.patch(f"/api/projects/{pid}", json={"sets": 2})
    pr = lambda: next(p for p in c.get("/api/board").json()["projects"] if p["id"] == pid)  # noqa: E731
    base = next(k for k in pr()["cards"] if k["plate_index"] == 1)
    mv = lambda lane, **kw: c.post("/api/cards/move", json={  # noqa: E731
        "card_id": base["id"], "project_id": pid, "lane": lane, "ids": [base["id"]], **kw}).json()
    mv("printing")
    assert mv("done", result="success")["plates_left"] == 0
    again = mv("analysed")
    assert (again["plates_left"], again["ok"]) == (1, 1)
    assert pr()["stats"]["left_g"] == 50
    assert pr()["stats"]["g"] == 100               # grand total: printed once + once more to do
    c.delete(f"/api/projects/{pid}")


def test_epics():
    c = TestClient(app)
    board = lambda: c.get("/api/board").json()  # noqa: E731
    e = c.post("/api/epics", json={"name": "Station AMS"}).json()
    p1 = c.post("/api/projects", json={"name": "Stacker", "epic_id": e["id"]}).json()
    p2 = c.post("/api/projects", json={"name": "Holder", "epic_id": e["id"]}).json()
    loose = c.post("/api/projects", json={"name": "Seul"}).json()
    b = board()
    assert [x["id"] for x in b["epics"]] == [e["id"]]
    proj = {p["id"]: p for p in b["projects"]}
    assert proj[p1["id"]]["epic_id"] == e["id"] and proj[loose["id"]]["epic_id"] is None

    # copies multiply: epic x2, project x3 -> 6 of each part
    c.post("/api/upload", data={"project_id": p1["id"], "role": "bambu"},
           files={"file": ("s.3mf", _zip(model_settings=AMS_SETTINGS, slice_info=AMS_SLICE))})
    c.patch(f"/api/projects/{p1['id']}", json={"sets": 3})
    c.patch(f"/api/epics/{e['id']}", json={"sets": 2})
    p = next(x for x in board()["projects"] if x["id"] == p1["id"])
    assert p["eff_sets"] == 6 and {x["name"]: x["need"] for x in p["parts"]}["Bras"] == 12

    # layout: move the loose project into the epic, first
    r = c.post("/api/layout", json={"top": [{"type": "epic", "id": e["id"]}],
                                    "epics": {str(e["id"]): [loose["id"], p1["id"], p2["id"]]}})
    assert r.status_code == 200
    ps = sorted((x for x in board()["projects"] if x["epic_id"] == e["id"]), key=lambda x: x["id"])
    assert len(ps) == 3
    # take it out again via the project sheet
    c.patch(f"/api/projects/{loose['id']}", json={"epic_id": 0})
    assert next(x for x in board()["projects"] if x["id"] == loose["id"])["epic_id"] is None

    # deleting the epic keeps its projects
    c.delete(f"/api/epics/{e['id']}")
    b = board()
    assert b["epics"] == [] and {p1["id"], p2["id"]} <= {x["id"] for x in b["projects"]}
    assert all(x["epic_id"] is None for x in b["projects"])
    for x in (p1, p2, loose):
        c.delete(f"/api/projects/{x['id']}")


def test_folders(tmp_path):
    c = TestClient(app)
    root = tmp_path / "3DModel"
    assert c.put("/api/settings", json={"models_root": str(root)}).json()["models_root_path"] == str(root)
    e = c.post("/api/epics", json={"name": "Station: AMS?"}).json()
    b = c.get("/api/board").json()
    assert next(x for x in b["epics"] if x["id"] == e["id"])["folder"] == str(root / "Station_ AMS_")

    # Bambu files saved in the epic's folder -> one project each, imported from disk
    ef = root / "Station_ AMS_"
    ef.mkdir(parents=True)
    (ef / "stacker.3mf").write_bytes(_zip(model_settings=AMS_SETTINGS, slice_info=SAVED_PROJECT_SLICE))
    (ef / "stacker.gcode.3mf").write_bytes(_zip(slice_info=ALL_PLATES_SLICE))
    (ef / "notes.txt").write_text("x")
    lst = c.get(f"/api/epics/{e['id']}/folder").json()
    assert [f["name"] for f in lst["files"]] == ["stacker.3mf"] and lst["files"][0]["project_id"] is None
    pid = c.post(f"/api/epics/{e['id']}/import", json={"names": ["stacker.3mf", "../../etc/passwd"]}).json()["projects"][0]
    pr = lambda: next(p for p in c.get("/api/board").json()["projects"] if p["id"] == pid)  # noqa: E731
    assert pr()["epic_id"] == e["id"] and pr()["folder"] == str(ef) and len(pr()["cards"]) == 2
    assert c.get(f"/api/epics/{e['id']}/folder").json()["files"][0]["project_id"] == pid

    # slicing export from the same folder, then a newer save is noticed and re-read
    files = c.get(f"/api/projects/{pid}/folder").json()["files"]
    assert {f["name"]: f["current"] for f in files} == {"stacker.3mf": True, "stacker.gcode.3mf": False}
    c.post(f"/api/projects/{pid}/import", json={"name": "stacker.gcode.3mf", "role": "slicing"})
    assert pr()["stats"]["time_s"] == 9000 and not pr()["src_changed"]
    import os as _os, time as _t
    later = _t.time() + 60
    _os.utime(ef / "stacker.gcode.3mf", (later, later))
    assert pr()["src_changed"]
    assert c.post(f"/api/projects/{pid}/refresh").json()["refreshed"] == ["slicing"]
    assert not pr()["src_changed"]
    assert c.post(f"/api/projects/{pid}/import", json={"name": "../x.3mf", "role": "bambu"}).status_code == 404

    # a project's own folder can be set by hand
    c.patch(f"/api/projects/{pid}", json={"folder": str(tmp_path / "ailleurs")})
    assert pr()["folder"] == str(tmp_path / "ailleurs")
    c.delete(f"/api/projects/{pid}")
    c.delete(f"/api/epics/{e['id']}")


def test_card_from_folder_stl(tmp_path):
    c = TestClient(app)
    c.put("/api/settings", json={"models_root": str(tmp_path)})
    pid = c.post("/api/projects", json={"name": "Boites"}).json()["id"]
    (tmp_path / "Boites").mkdir()
    (tmp_path / "Boites" / "petite boite.stl").write_bytes(b"solid x\nendsolid x\n")
    k = c.post(f"/api/projects/{pid}/card-from-file", json={"name": "petite boite.stl"}).json()
    assert k["title"] == "petite boite" and k["files"][0]["kind"] == "stl"
    assert c.post(f"/api/projects/{pid}/card-from-file", json={"name": "../x.stl"}).status_code == 404
    c.delete(f"/api/projects/{pid}")
