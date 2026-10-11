"""Importer: .apk, .xapk and the download path (local HTTP server, synthetic app file, no Line 6 data)."""
import functools, http.server, io, tempfile, threading, zipfile
from pathlib import Path

import firehawk.importer as im


def fake_apk() -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("assets/amps.models", "[]")
        z.writestr("assets/AmpCatalog.json", "{}")
        z.writestr("assets/defaultSymbolTable_Firebird.bin", b"\0" * 8)
        z.writestr("classes.dex", b"")
    return b.getvalue()


def main():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "app.apk").write_bytes(fake_apk())
    with zipfile.ZipFile(tmp / "app.xapk", "w") as z:          # bundle with the base apk inside
        z.writestr("com.line6.firehawk.apk", fake_apk())
        z.writestr("manifest.json", "{}")
    (tmp / "page.html").write_text("<html>captcha</html>")
    (tmp / "other.apk").write_bytes(b"PK\5\6" + b"\0" * 18)   # empty zip: some other app

    assert im.import_apk(tmp / "app.apk", tmp / "d1") == 1 and (tmp / "d1/models/AmpCatalog.json").exists()
    assert im.import_apk(tmp / "app.xapk", tmp / "d2") == 1 and (tmp / "d2/defaultSymbolTable_Firebird.bin").exists()
    try:
        im.import_apk(tmp / "other.apk", tmp / "d4")
        raise AssertionError("accepted a non-Firehawk app")
    except ValueError:
        pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(
        type("Q", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}), directory=str(tmp)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    im.data_dir = lambda: tmp / "d3"
    seen = []
    n = im.download_and_import(lambda d, t: seen.append((d, t)), [f"{base}/page.html", f"{base}/app.xapk"])
    assert n == 1 and (tmp / "d3/models/amps.models").exists() and seen[-1][0] == seen[-1][1]
    try:
        im.download_and_import(None, [f"{base}/page.html"])
        raise AssertionError("accepted an HTML page")
    except RuntimeError as e:
        assert "instead of an app file" in str(e)
    srv.shutdown()
    print("importer OK")


if __name__ == "__main__":
    main()
