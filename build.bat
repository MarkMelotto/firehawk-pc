@echo off
REM Rebuild FirehawkRemote.exe and firehawk-cli.exe (needs Python 3.10+ on PATH)
pip install pyinstaller pyserial pillow || exit /b 1
pyinstaller --noconfirm --onefile --windowed --name FirehawkRemote --icon firehawk.ico --add-data "firehawk/*.bin;firehawk" --add-data "firehawk/assets;firehawk/assets" --hidden-import PIL._tkinter_finder --hidden-import serial.tools.list_ports run_gui.py || exit /b 1
pyinstaller --noconfirm --onefile --console --name firehawk-cli --add-data "firehawk/*.bin;firehawk" run_cli.py || exit /b 1
echo Done: see dist\
