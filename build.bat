@echo off
if not exist assets mkdir assets
.venv\Scripts\python.exe -m acs.icon assets\icon.ico
.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --noconsole --distpath out --name AutoAcceptCS2 --icon assets\icon.ico --exclude-module tkinter autoaccept.pyw
