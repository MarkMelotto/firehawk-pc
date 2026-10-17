"""Import Line 6 model definitions + symbol tables from the user's own Firehawk Remote APK.

Nothing from Line 6 ships with this app; the data lives in a per-user folder.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

TABLES = ["defaultSymbolTable_Firebird.bin", "defaultSymbolTable_FX100.bin", "defaultSymbolTable.bin"]


def data_dir() -> Path:
    """%LOCALAPPDATA%\\FirehawkRemote on Windows, ~/.firehawk-remote elsewhere."""
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "FirehawkRemote" if base else Path.home() / ".firehawk-remote"


def models_dir() -> Path:
    return data_dir() / "models"


def has_line6_data() -> bool:
    """Models, picker groups and slider layouts present (older imports lack *Catalog.json / sliders.json)."""
    d = models_dir()
    return any(d.glob("*.models")) and (d / "AmpCatalog.json").exists() and (d / "sliders.json").exists()


# official Line 6 app on Google Play; these mirrors serve it as a direct download (tried in order)
PACKAGE = "com.line6.firehawk"
MIRRORS = [f"https://d.apkpure.com/b/APK/{PACKAGE}?version=latest",
           f"https://d.apkpure.net/b/APK/{PACKAGE}?version=latest"]
DOWNLOAD_PAGE = "https://firehawk-remote.en.softonic.com/android"


def _find_assets_zip(z: zipfile.ZipFile, depth: int = 0) -> Optional[zipfile.ZipFile]:
    """The APK itself, or the base APK inside an .xapk/.apkm bundle."""
    if any(n.startswith("assets/") and n.endswith(".models") for n in z.namelist()):
        return z
    if depth < 2:
        for n in z.namelist():
            if n.lower().endswith(".apk"):
                inner = _find_assets_zip(zipfile.ZipFile(io.BytesIO(z.read(n))), depth + 1)
                if inner:
                    return inner
    return None


SLIDER_LIBS = ["lib/x86/libSlidersLogicLib.so", "lib/armeabi-v7a/libSlidersLogicLib.so",
               "lib/armeabi/libSlidersLogicLib.so"]


def _find_member(z: zipfile.ZipFile, names, depth: int = 0) -> Optional[bytes]:
    """First of `names` in the zip or in an .apk nested in it (split APKs keep native libs separately)."""
    have = set(z.namelist())
    for n in names:
        if n in have:
            return z.read(n)
    if depth < 2:
        for n in z.namelist():
            if n.lower().endswith(".apk"):
                r = _find_member(zipfile.ZipFile(io.BytesIO(z.read(n))), names, depth + 1)
                if r:
                    return r
    return None


def _import_sliders(outer: zipfile.ZipFile, dest: Path) -> None:
    """How the app displays each parameter (units, ranges, order) -> sliders.json. Optional."""
    import json
    from .sliders import extract
    so = _find_member(outer, SLIDER_LIBS)
    if not so:
        return
    ids = {}
    for f in (dest / "models").glob("*.models"):
        for m in json.loads(f.read_text(encoding="utf-8")):
            if "id" in m:
                ids[m["id"]] = m["symbolicID"]
    try:
        (dest / "models" / "sliders.json").write_text(json.dumps(extract(so, ids)), encoding="utf-8")
    except ValueError:
        pass


def import_apk(apk: "str | Path", dest: "Path | None" = None) -> int:
    """Copy assets/*.models, picker groups and cached symbol tables out of the APK (or .xapk/.apkm).
    Returns the number of model files."""
    dest = dest or data_dir()
    with zipfile.ZipFile(apk) as outer:
        z = _find_assets_zip(outer)
        if z is None:
            raise ValueError("This file doesn't contain Firehawk model files - is it the Line 6 Firehawk Remote app?")
        names = z.namelist()
        models = [n for n in names if n.startswith("assets/") and n.endswith(".models")]
        if "assets/AmpCatalog.json" not in names:
            raise ValueError("This version of the app has no model catalog; use Firehawk Remote 1.31 or newer.")
        (dest / "models").mkdir(parents=True, exist_ok=True)
        for n in models:
            (dest / "models" / Path(n).name).write_bytes(z.read(n))
        for n in names:                                   # picker groups (AmpCatalog.json, FXCatalog.json, ...)
            if n.startswith("assets/") and n.endswith("Catalog.json"):
                (dest / "models" / Path(n).name).write_bytes(z.read(n))
        for t in TABLES:
            if f"assets/{t}" in names:
                (dest / t).write_bytes(z.read(f"assets/{t}"))
        _import_sliders(outer, dest)
    return len(models)


def download_and_import(progress: Optional[Callable[[int, int], None]] = None, mirrors=MIRRORS) -> int:
    """Download the official app from a mirror into a temp file, import it, delete it.
    progress(done_bytes, total_bytes or 0). Raises RuntimeError with every mirror's error if all fail."""
    errors = []
    for url in mirrors:
        fd, tmp = tempfile.mkstemp(suffix=".apk")
        os.close(fd)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
                total, done = int(r.headers.get("Content-Length") or 0), 0
                first = r.read(4)
                if first[:2] != b"PK":                      # an HTML/captcha page instead of the app
                    raise ValueError(f"got {r.headers.get('Content-Type', '?')} instead of an app file")
                f.write(first)
                done = len(first)
                while chunk := r.read(1 << 16):
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
            return import_apk(tmp)
        except Exception as e:  # noqa: BLE001 - try the next mirror
            errors.append(f"{url.split('/')[2]}: {e}")
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    raise RuntimeError("Automatic download failed:\n" + "\n".join(errors))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m firehawk.importer path/to/firehawk.apk | --download")
    n = download_and_import() if sys.argv[1] == "--download" else import_apk(sys.argv[1])
    print(f"imported {n} model files into {data_dir()}")
