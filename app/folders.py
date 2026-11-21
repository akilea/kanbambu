"""Folders on the machine running kanbambu that hold each epic's and project's files.

Default layout, under the models root (setting, default ~/Documents/3DModel):
    <root>/<epic>/<project>/    for a project in an epic
    <root>/<project>/           for a project on its own
A folder can also be set by hand. The server reads these folders directly, so a Bambu file
saved there can be (re)imported in one click, and "open" shows the folder on that machine.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

DEFAULT_ROOT = os.path.join("~", "Documents", "3DModel")
MODEL_EXT = (".3mf", ".stl", ".step", ".stp", ".obj", ".f3d", ".scad")
MAX_FILES = 300


def get_setting(c, key: str, default: str = "") -> str:
    r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r and r["value"] is not None else default


def set_setting(c, key: str, value: str):
    c.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def models_root(c) -> str:
    return os.path.expanduser(get_setting(c, "models_root") or DEFAULT_ROOT)


def safe_dir(name: str) -> str:
    """A folder name valid on Windows: no <>:"/\\|?* and no trailing dot or space."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", (name or "").strip()).rstrip(". ")
    return s[:120] or "Sans nom"


def epic_folder(c, e) -> str:
    return os.path.expanduser(e["folder"]) if e["folder"] else os.path.join(models_root(c), safe_dir(e["name"]))


def project_folder(c, p) -> str:
    if p["folder"]:
        return os.path.expanduser(p["folder"])
    e = c.execute("SELECT * FROM epics WHERE id=?", (p["epic_id"],)).fetchone() if p["epic_id"] else None
    return os.path.join(epic_folder(c, e) if e else models_root(c), safe_dir(p["name"]))


def kind(name: str) -> str:
    n = name.lower()
    if n.endswith(".gcode.3mf"):
        return "gcode3mf"
    return n.rsplit(".", 1)[-1]


def list_models(folder: str) -> list:
    """Model files in the folder and its subfolders (2 levels), newest first."""
    out = []
    if not os.path.isdir(folder):
        return out
    base = os.path.abspath(folder)
    for root, dirs, files in os.walk(base):
        depth = os.path.relpath(root, base).count(os.sep) + (0 if root == base else 1)
        if depth >= 2:
            dirs[:] = []
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.lower().endswith(MODEL_EXT):
                full = os.path.join(root, f)
                st = os.stat(full)
                out.append({"name": os.path.relpath(full, base).replace(os.sep, "/"), "kind": kind(f),
                            "size": st.st_size, "mtime": st.st_mtime})
                if len(out) >= MAX_FILES:
                    return sorted(out, key=lambda x: -x["mtime"])
    return sorted(out, key=lambda x: -x["mtime"])


def resolve_inside(folder: str, rel: str) -> str:
    """Path of a file named relative to the folder; refuses anything outside it."""
    base = os.path.realpath(folder)
    full = os.path.realpath(os.path.join(base, rel))
    if os.path.commonpath([base, full]) != base or not os.path.isfile(full):
        raise FileNotFoundError(rel)
    return full


def open_folder(folder: str):
    """Show the folder in the file manager of the machine running kanbambu (created if missing).
    Only ever opens a directory, never a file."""
    os.makedirs(folder, exist_ok=True)
    if not os.path.isdir(folder):
        raise NotADirectoryError(folder)
    if sys.platform.startswith("win"):
        os.startfile(folder)  # noqa: S606 - a directory: opens Explorer
    elif sys.platform == "darwin":
        subprocess.Popen(["open", folder])
    else:
        subprocess.Popen(["xdg-open", folder], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def changed(path, mtime) -> bool:
    """The folder file a project was imported from was saved again since."""
    try:
        return bool(path) and mtime is not None and os.path.getmtime(path) > mtime + 1
    except OSError:
        return False

