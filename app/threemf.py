"""Read slicing data and thumbnails out of Bambu Studio 3MF files.

A plate exported with "Export plate sliced file" (.gcode.3mf) carries
Metadata/slice_info.config with print time, weight and per-filament usage.
A plain project .3mf only carries thumbnails (Metadata/plate_N.png).
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
import zipfile
from typing import Optional


def _meta(el: ET.Element) -> dict:
    return {m.get("key"): m.get("value") for m in el.findall("metadata")}


def _num(v, cast=float, default=0):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return default


def parse_3mf(path: str) -> dict:
    """Return {"analysis": dict|None, "thumbnail": bytes|None}."""
    out: dict = {"analysis": None, "thumbnail": None}
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        return out
    with z:
        names = set(z.namelist())
        out["thumbnail"] = _thumbnail(z, names)
        if "Metadata/slice_info.config" in names:
            out["analysis"] = _slice_info(z, names)
    return out


def _thumbnail(z: zipfile.ZipFile, names: set) -> Optional[bytes]:
    for cand in ("Metadata/plate_1.png", "Metadata/thumbnail.png",
                 "Metadata/plate_1_small.png", "Metadata/top_1.png"):
        if cand in names:
            return z.read(cand)
    pngs = sorted(n for n in names if n.startswith("Metadata/plate_") and n.endswith(".png"))
    return z.read(pngs[0]) if pngs else None


def _profile_names(z: zipfile.ZipFile, names: set) -> list:
    if "Metadata/project_settings.config" not in names:
        return []
    try:
        cfg = json.loads(z.read("Metadata/project_settings.config"))
        ids = cfg.get("filament_settings_id") or []
        return ids if isinstance(ids, list) else []
    except (ValueError, KeyError):
        return []


def _slice_info(z: zipfile.ZipFile, names: set) -> Optional[dict]:
    try:
        root = ET.fromstring(z.read("Metadata/slice_info.config"))
    except ET.ParseError:
        return None
    profiles = _profile_names(z, names)
    plates = []
    totals: dict = {}
    objects = 0
    for plate in root.findall("plate"):
        m = _meta(plate)
        objects += sum(1 for o in plate.findall("object") if o.get("skipped", "false") != "true")
        fils = []
        for f in plate.findall("filament"):
            fid = _num(f.get("id"), int, 0)
            fil = {
                "id": fid,
                "type": f.get("type") or "",
                "color": (f.get("color") or "#888888")[:7],
                "g": round(_num(f.get("used_g")), 2),
                "m": round(_num(f.get("used_m")), 2),
                "profile": profiles[fid - 1] if 0 < fid <= len(profiles) else "",
            }
            fils.append(fil)
            key = (fil["color"].upper(), fil["type"])
            t = totals.setdefault(key, {"color": fil["color"], "type": fil["type"],
                                        "profile": fil["profile"], "g": 0.0, "m": 0.0})
            t["g"] = round(t["g"] + fil["g"], 2)
            t["m"] = round(t["m"] + fil["m"], 2)
        plates.append({
            "index": _num(m.get("index"), int, len(plates) + 1),
            "time_s": _num(m.get("prediction"), int, 0),
            "g": round(_num(m.get("weight")), 2),
            "filaments": fils,
        })
    if not plates:
        return None
    return {
        "time_s": sum(p["time_s"] for p in plates),
        "g": round(sum(p["g"] or sum(f["g"] for f in p["filaments"]) for p in plates), 2),
        "plates": plates,
        "filaments": sorted(totals.values(), key=lambda t: -t["g"]),
        "objects": objects,
    }
