# VERONICA: notes for Claude Code

VERONICA is the owner's single personal assistant ("she"): Claude Opus 5.5, local Whisper, Fish Audio voice, the
orb window (`VERONICA.exe` -> `.venv\Scripts\pythonw.exe app.py`), Gmail and Calendar, code tools, research.

- **One AI tool per project (owner, 2026-10-12):** Claude Code is the one tool that writes VERONICA. Other AI tools
  (Qwen Code, OpenClaw, Hermes, ...) do not edit this folder.
- **Her home is `D:\VERONICA`** (moved out of OneDrive on 2026-10-12). Older copies are archives under
  `D:\JARVIS-release-work\outside-repo\`; do not edit or run them.
- **She is separate from JARVIS.** Never touch `C:\Users\jonny\JARVIS-claude-workspace` or the installed JARVIS from
  here; `safety.py` refuses those folders in code.
- **Keys** are in `.env` (git-ignored). Never open, print or commit it. `data\` (memories, checkpoints) and `models\`
  are git-ignored too.
- **Tests:** run them on a throwaway copy of the tracked files with `D:\VERONICA\.venv\Scripts\python.exe -m pytest -q`;
  run in place, the checkpoint code writes test conversations into her real `data\checkpoints`.
- **Known state (see the first commit):** on 2026-10-11 Qwen Code made her "fully autonomous": `send_email` sends
  without approval, `calendar_add`/`calendar_edit` only pretend, keep/undo is gone, and the README still describes
  the earlier draft-only design. **Owner's decision, 2026-10-12: keep her autonomous** ("keep it as it is"): she
  sends email without asking. Do not put an approval step back unless the owner asks.
