"""Import Line 6 model definitions + symbol tables from the user's own Firehawk Remote APK.

Nothing from Line 6 ships with this app; the data lives in a per-user folder.
"""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

TABLES = ["defaultSymbolTable_Firebird.bin", "defaultSymbolTable_FX100.bin", "defaultSymbolTable.bin"]


def data_dir() -> Path:
    """%LOCALAPPDATA%\\FirehawkRemote on Windows, ~/.firehawk-remote elsewhere."""
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "FirehawkRemote" if base else Path.home() / ".firehawk-remote"


def models_dir() -> Path:
    return data_dir() / "models"


def has_line6_data() -> bool:
    return any(models_dir().glob("*.models"))


def import_apk(apk: "str | Path", dest: "Path | None" = None) -> int:
    """Copy assets/*.models and the cached symbol tables out of the APK. Returns number of model files."""
    dest = dest or data_dir()
    (dest / "models").mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(apk) as z:
        names = z.namelist()
        models = [n for n in names if n.startswith("assets/") and n.endswith(".models")]
        if not models:
            raise ValueError("This APK doesn't contain Firehawk model files - is it the Line 6 Firehawk Remote app?")
        for n in models:
            (dest / "models" / Path(n).name).write_bytes(z.read(n))
        for n in names:                                   # picker groups (AmpCatalog.json, FXCatalog.json, ...)
            if n.startswith("assets/") and n.endswith("Catalog.json"):
                (dest / "models" / Path(n).name).write_bytes(z.read(n))
        for t in TABLES:
            if f"assets/{t}" in names:
                (dest / t).write_bytes(z.read(f"assets/{t}"))
    return len(models)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m firehawk.importer path/to/firehawk.apk")
    print(f"imported {import_apk(sys.argv[1])} model files into {data_dir()}")
