@echo off
REM Build dist\FirehawkRemote.exe and dist\firehawk-cli.exe locally (needs Python 3.10+ on PATH).
REM Releases are normally built by GitHub: push a tag like v1.0.1.
pip install -r requirements.txt pyinstaller || exit /b 1
python -m firehawk.make_icon firehawk.ico || exit /b 1
pyinstaller --noconfirm --onefile --windowed --name FirehawkRemote --icon firehawk.ico --hidden-import serial.tools.list_ports --hidden-import PIL._tkinter_finder run_gui.py || exit /b 1
pyinstaller --noconfirm --onefile --console --name firehawk-cli run_cli.py || exit /b 1
echo Done: see dist\
