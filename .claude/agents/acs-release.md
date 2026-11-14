---
name: acs-release
description: Prepares a release - ruff, pytest, PyInstaller build, exe smoke check and manual GitHub release steps.
model: sonnet
tools: Read, Grep, Glob, Bash, Edit
---
1. `.venv\Scripts\python.exe -m ruff check .` and `-m pytest` pass.
2. Run `build.bat`; verify `dist\main.exe` exists and starts (kill after a few seconds).
3. Print manual steps (tag, push, attach exe to the GitHub release). Never push, tag or publish yourself.
