# VERONICA: notes for Claude Code

VERONICA is the owner's single personal assistant ("she"): Claude Opus 5.5, local Whisper, Fish Audio voice, the
orb window (`VERONICA.exe` -> `.venv\Scripts\pythonw.exe app.py`), Gmail and Calendar, code tools, research.

- **One AI tool per project (owner, 2026-10-12):** Claude Code is the one tool that writes VERONICA. Other AI tools
  (Qwen Code, OpenClaw, Hermes, ...) do not edit this folder.
- **Her home is `C:\Users\jonny\JARVIS-claude-workspace\veronica`** (out of OneDrive and into `D:\VERONICA` on
  2026-10-12, then into the JARVIS workspace the same day: merge plan D10). Older copies are archives under
  `D:\JARVIS-release-work\outside-repo\`; do not edit or run them.
- **She is joining JARVIS** (`..\repo\docs\VERONICA-MERGE-PLAN.md`, approved 2026-10-12): her persona and skills are
  being built into JARVIS's Core. This app keeps running as it is until phase 4 retires it on the owner's word. Her code
  tools still never touch JARVIS's folders or the installed JARVIS: `safety.py` refuses them in code.
- **Keys** are in `.env` (git-ignored). Never open, print or commit it. `data\` (memories, checkpoints) and `models\`
  are git-ignored too.
- **Tests:** run them on a throwaway copy of the tracked files with this folder's `.venv\Scripts\python.exe -m pytest -q`;
  run in place, the checkpoint code writes test conversations into her real `data\checkpoints`.
- **Known state (see the first commit):** on 2026-10-11 Qwen Code made her "fully autonomous": `send_email` sends
  without approval, `calendar_add`/`calendar_edit` only pretend, keep/undo is gone, and the README still describes
  the earlier draft-only design. **Owner's decision, 2026-10-12: keep her autonomous** ("keep it as it is"): she
  sends email without asking. Do not put an approval step back unless the owner asks.
