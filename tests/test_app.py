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
    assert card["lane"] == "park"

    # plain project 3MF: thumbnail only, stays in Park
    r = c.post("/api/upload", data={"card_id": card["id"], "role": "attach"},
               files={"file": ("base.3mf", make_3mf(False))})
    assert r.status_code == 200
    card = next(k for k in c.get("/api/board").json()["projects"][0]["cards"])
    assert card["lane"] == "park" and card["preview"] and card["analysis"] is None
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
    assert cards[2]["title"] == "Plateau 2" and (cards[2]["qty"], cards[2]["plates"]) == (2, 1)
    assert cards[3]["lane"] == "park"
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
