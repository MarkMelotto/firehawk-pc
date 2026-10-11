"""Write the app icon (.ico) from the original glyph.  python -m firehawk.make_icon firehawk.ico"""
import sys

from .icons import app_icon_png

if __name__ == "__main__":
    app_icon_png(256).save(sys.argv[1] if len(sys.argv) > 1 else "firehawk.ico",
                           sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
