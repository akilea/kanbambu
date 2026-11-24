"""Read slicing data and thumbnails out of Bambu Studio 3MF files.

- Metadata/slice_info.config: print time, weight and per-filament usage of each
  sliced plate (present in .gcode.3mf exports and in projects saved after slicing).
- Metadata/model_settings.config: the plates of a project (name, objects), sliced or not.
- Metadata/plate_N.png: Bambu Studio's preview of plate N.
- Metadata/project_settings.config: filament profile names and cost per kg.
"""
from __future__ import annotations

import json
import re
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


def part_name(name: str) -> str:
    """Object name as a part name: no file extension, no surrounding spaces. Bambu Studio numbers
    the copies of an imported object ("Pillar.stl 42"): they are all the same part."""
    return re.sub(r"\.(stl|3mf|step|stp|obj)(\s+\d+)?$", "", (name or "").strip(), flags=re.I).strip() or "Pièce"


def _count(names) -> dict:
    out: dict = {}
    for n in names:
        k = part_name(n)
        out[k] = out.get(k, 0) + 1
    return out


def _xml(z: zipfile.ZipFile, names: set, name: str) -> Optional[ET.Element]:
    if name not in names:
        return None
    try:
        return ET.fromstring(z.read(name))
    except ET.ParseError:
        return None


def parse_3mf(path: str) -> dict:
    """Return {
        "analysis": all sliced plates merged (dict) or None,
        "thumbnail": first plate preview (bytes) or None,
        "plates": [{index, name, objects, parts, thumbnail, analysis}] for every plate of the project,
        "parts": {object name: count} over the whole project,
    }"""
    out: dict = {"analysis": None, "thumbnail": None, "plates": [], "parts": {}}
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        return out
    with z:
        names = set(z.namelist())
        settings = _settings(z, names)
        sliced = _sliced_plates(z, names, settings)          # {index: analysis}
        layout = _layout_plates(z, names)                    # {index: {name, objects}}
        indices = sorted(set(sliced) | set(layout))
        for i in indices:
            lay = layout.get(i, {})
            a = sliced.get(i)
            parts = (a or {}).get("parts") or lay.get("parts") or {}
            for k, v in parts.items():
                out["parts"][k] = out["parts"].get(k, 0) + v
            out["plates"].append({
                "index": i,
                "name": lay.get("name") or "",
                "objects": (a or {}).get("objects") or lay.get("objects", 0),
                "parts": parts,
                "thumbnail": _plate_thumb(z, names, i),
                "analysis": a,
            })
        out["thumbnail"] = next((p["thumbnail"] for p in out["plates"] if p["thumbnail"]), None) \
            or _any_thumb(z, names)
        if sliced:
            out["analysis"] = merge([sliced[i] for i in sorted(sliced)])
    return out


# ---------- pieces ----------

def _settings(z, names) -> dict:
    if "Metadata/project_settings.config" not in names:
        return {}
    try:
        cfg = json.loads(z.read("Metadata/project_settings.config"))
        return cfg if isinstance(cfg, dict) else {}
    except ValueError:
        return {}


def _at(lst, i, default=""):
    return lst[i] if isinstance(lst, list) and 0 <= i < len(lst) else default


def _sliced_plates(z, names, settings) -> dict:
    root = _xml(z, names, "Metadata/slice_info.config")
    if root is None:
        return {}
    profiles = settings.get("filament_settings_id") or []
    costs = settings.get("filament_cost") or []
    out = {}
    for n, plate in enumerate(root.findall("plate"), 1):
        m = _meta(plate)
        idx = _num(m.get("index"), int, n)
        fils = []
        for f in plate.findall("filament"):
            fid = _num(f.get("id"), int, 0)
            g = round(_num(f.get("used_g")), 2)
            cost_kg = _num(_at(costs, fid - 1, 0))
            fils.append({
                "id": fid,
                "type": f.get("type") or "",
                "color": (f.get("color") or "#888888")[:7],
                "g": g,
                "m": round(_num(f.get("used_m")), 2),
                "profile": _at(profiles, fid - 1),
                "cost": round(g / 1000 * cost_kg, 2),
            })
        time_s = _num(m.get("prediction"), int, 0)
        if not time_s and not fils:
            continue  # plate listed but not sliced
        weight = round(_num(m.get("weight")), 2) or round(sum(f["g"] for f in fils), 2)
        a = {
            "time_s": time_s,
            "g": weight,
            "cost": round(sum(f["cost"] for f in fils), 2),
            "objects": sum(1 for o in plate.findall("object") if o.get("skipped", "false") != "true"),
            "parts": _count(o.get("name") or "" for o in plate.findall("object")
                            if o.get("skipped", "false") != "true"),
            "plates": [{"index": idx, "time_s": time_s, "g": weight}],
            "filaments": sorted(fils, key=lambda f: -f["g"]),
        }
        out[idx] = a
    return out


def _layout_plates(z, names) -> dict:
    root = _xml(z, names, "Metadata/model_settings.config")
    if root is None:
        return {}
    names = {o.get("id"): _meta(o).get("name") or "" for o in root.findall("object")}
    out = {}
    for n, plate in enumerate(root.findall("plate"), 1):
        m = _meta(plate)
        idx = _num(m.get("plater_id"), int, n)
        inst = [_meta(i).get("object_id") for i in plate.findall("model_instance")]
        out[idx] = {"name": (m.get("plater_name") or "").strip(),
                    "objects": len(inst),
                    "parts": _count(names[o] for o in inst if names.get(o))}
    return out


def _plate_thumb(z, names, i) -> Optional[bytes]:
    for cand in (f"Metadata/plate_{i}.png", f"Metadata/plate_{i}_small.png", f"Metadata/top_{i}.png"):
        if cand in names:
            return z.read(cand)
    return None


def _any_thumb(z, names) -> Optional[bytes]:
    if "Metadata/thumbnail.png" in names:
        return z.read("Metadata/thumbnail.png")
    pngs = sorted(n for n in names if n.startswith("Metadata/plate_") and n.endswith(".png"))
    return z.read(pngs[0]) if pngs else None


def merge(analyses: list) -> dict:
    """Combine several plate analyses (or card analyses) into one, filaments grouped by slot/color."""
    totals: dict = {}
    parts: dict = {}
    for a in analyses:
        for k, v in (a.get("parts") or {}).items():
            parts[k] = parts.get(k, 0) + v
        for f in a["filaments"]:
            key = (f.get("id"), f["color"].upper(), f["type"])
            t = totals.setdefault(key, {**f, "g": 0.0, "m": 0.0, "cost": 0.0})
            t["g"] = round(t["g"] + f["g"], 2)
            t["m"] = round(t["m"] + f["m"], 2)
            t["cost"] = round(t["cost"] + f.get("cost", 0), 2)
    return {
        "time_s": sum(a["time_s"] for a in analyses),
        "g": round(sum(a["g"] for a in analyses), 2),
        "cost": round(sum(a.get("cost", 0) for a in analyses), 2),
        "objects": sum(a.get("objects", 0) for a in analyses),
        "parts": parts,
        "plates": [p for a in analyses for p in a["plates"]],
        "filaments": sorted(totals.values(), key=lambda t: -t["g"]),
    }
