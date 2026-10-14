# VERONICA: notes for Claude Code

VERONICA is the owner's single personal assistant ("she"): Claude Opus 5.5, local Whisper, Fish Audio voice, the
orb window (`VERONICA.exe` -> `.venv\Scripts\pythonw.exe app.py`), Gmail and Calendar, code tools, research.

- **Models, locked in `models.py` (owner's OpenRouter model guide, 2026-10-13 evening; replaces the Qwen-only
  decision of that morning):** Claude Sonnet 5.5 for chat and research, Claude Opus 5.5 for the coder subagent,
  Claude Haiku 5.5 for the researcher/secretary subagents, Gemini 3.1 Flash-Lite as the backup from another maker
  (tried after two provider failures in a row, `agent.BACKUP_AFTER`). The key's guardrail was opened to these the
  same evening (probed: all five answer). `OPENROUTER_MODEL` / `_CODING_MODEL` / `_RESEARCH_MODEL` may only pick
  another model from `models.ALLOWED`; anything else is ignored. Tests: `test_models.py`.
  Extra (owner, 2026-10-14): ASI:One `asi1-mini` is in `ALLOWED`, served at `https://api.asi1.ai/v1` with
  `ASI_ONE_API_KEY` (`models.endpoint`, `agent._client_for`); OpenRouter's `reasoning` extra_body is not sent to it.
  Not a default for any job; picked only by an `OPENROUTER_*_MODEL` line (new: `OPENROUTER_HELPER_MODEL`).
  Helpers changed (owner, 2026-10-14): `xiaomi/mimo-v2.6-flash` ($0.14/$0.28, 1M context; probed on the key: tool
  calls work with and without her `reasoning` setting) replaces Haiku 5.5 as the researcher/secretary model; Haiku
  stays in `ALLOWED`.
- **(Superseded) Qwen only, through OpenRouter (owner, 2026-10-13: "can we make it only use qwen models for all the functions?",
  "and veronica as well"; "DONT CHANGE ANY SAFTEY RULES FOR VERONICA OR JARVIS / keep fish audio api for both of
  them"):** her chat (`agent.py`, the `openai` package) and her research (`research.py`, `langchain_openai`) use
  `qwen/qwen3.8-max-0902` at `https://openrouter.ai/api/v1`, key `OPENROUTER_API_KEY` in `.env` (the owner's to
  place). `OPENROUTER_MODEL` and `OPENROUTER_RESEARCH_MODEL` lines can name another model; the old `VERONICA_MODEL`
  line (a Groq model) is no longer read. The key is restricted to `qwen/*` by an OpenRouter guardrail. Fish Audio,
  local Whisper and `safety.py` unchanged. Before: Groq for chat (2026-10-12, "switch groq"), Claude for research;
  the Claude chat version is kept as `agent.py.claude-backup`.
- **Her hands (Hermes, 2026-10-13, stage 1 of "autonomous, self-repairing software engineer"; owner chose no gate
  on commands):** `hands.py` gives the chat `run_command` (any shell command, waits, exit code + output, process tree
  killed at the timeout), `start_process`/`process_output`/`stop_process`/`list_processes` (background programs
  logging to temp files), and `web_search`/`fetch_url` over `web.py`. Output goes through `changes.hide_secrets`.
  The `coder` subagent has these tools and uses `OPENROUTER_CODING_MODEL` when set (`agent.coding_model()`); the
  `researcher`'s tools now exist. Tests: `test_hands.py`.
- **Stage 2, self-repair (2026-10-14, commit 09b4466):** `health.py`: health check at window start and `self_check`;
  `start_self_repair`/`finish_self_repair`/`rollback_self_repair`: snapshot in `data\self-repair`, test baseline,
  branch `self-repair/<stamp>` (no checkout), automatic rollback on a new failing test or failed check. Tests:
  `test_health.py`. Verified live on a throwaway copy (she fixed a planted bug in `models.label()`).
- **Stage 3, goals + proposals + heartbeat (2026-10-14; owner 2026-10-13: every change and goal she proposes needs
  his approval first):** `goals.py` + tables `goals`/`proposals`/`heartbeats` in `store.py`. Her tools:
  `propose_goal`, `propose_change` (fix/improvement/task), `goal_progress` and `finish_proposal` (refused unless the
  item is approved/active), `list_goals`. She has NO tool that approves: only the owner's own words, read by
  `agent.respond` before the model ("approve proposal 3", "decline goal 2", "pause/resume/drop goal N", "goal N is
  done", "new goal: ..."), and the window's Goals panel (`Bridge.decide`/`add_goal`) move items. Heartbeat runs set
  `owner_present = False`, so text inside them never decides. Heartbeat (`app.Session._beat_loop`, while the window
  is open; settings in `data\heartbeat.json`, default hourly, minimum 15 min; or `python goals.py beat`): health
  check (a failing check becomes a pending fix proposal, keys excepted, a declined one quiet for 7 days), one step of
  approved work (approved proposals first, each at most 3 heartbeats), and a daily review that may propose up to 2
  ideas. Each run is a fresh conversation capped at 40 model steps (review 15). Tests: `test_goals.py`.
- **Stage 4, her hands on the PC (2026-10-14; owner: everything, any time, yes/no before each action while he is
  there):** `pc.py` (ctypes only, no new packages): screenshots (BitBlt -> png), list/activate/minimize/close windows,
  click, type, press keys, scroll, open files/urls/system pages, volume, media keys, lock, Windows toast.
  `look_at_screen` sends the screenshot to the chat model as an image and returns its answer. Gate in `agent._pc_gate`:
  with the owner present every changing action waits (`pending_pc`, his yes/no in words or the window's Allow/Deny);
  in heartbeat runs it runs at once and is recorded as a heartbeat row of kind `pc`, unless the `hands` switch
  (`data\heartbeat.json`, Goals and Hands panels) is off; "stop her hands"/"hands on" and the Hands panel switch
  override everything. She must never click password boxes, payment pages or email Send. Tests: `test_pc.py`.
- **Interface upgrade (2026-10-14):** markdown in the thread (headings, lists, bold/italic, links, fenced code with a
  Copy button), timestamps, multi-line input (Enter sends, Shift+Enter newline), Ctrl+K command palette, Ctrl+L new
  chat, a Hands screen (switches, the waiting action with Allow/Deny, the log of what her hands did) and a waiting
  action bar on the chat. Planned next: the VERONICA/JARVIS merge (repo\docs\VERONICA-MERGE-PLAN.md).
- **The neon dashboard look (owner's mockup, 2026-10-14: "give veronica this exact look"):** `ui\index.html` rebuilt
  around the same script: a 220 px sidebar (Chat, Tools, Projects, Goals, Memory, Research, Hands; Settings and the
  "Veronica — Online • Ready" card at the bottom; icons only under 900 px, the window's minimum is 380), the home orb
  with orbit rings driven by her idle/listening/thinking/speaking state, "Hey, I'm Veronica. / Always on your side.",
  quick chips, the round message bar, and a right column (live clock, Focus Mode, Current Tasks from `api.tasks()`,
  System Status from `info`: memory count, connections, heartbeat). **Focus Mode = `listen_for_name` off** (the only
  listening switch outside voice mode; `pause` only works inside it); the Settings "Listen for Veronica" switch is its
  mirror. Tools is a card grid: each card opens a screen or puts a ready prompt in the message bar, nothing is sent
  until Enter. Research is now a screen (`#research`), not a sheet. Goal progress bars show heartbeats (her data has
  no percentage). No fonts or scripts from the network; every `pywebview.api` call is guarded, so the page renders in
  a plain browser (`#chat`, `#tools`, ... preview hashes). Smoke keeps `#home.active`, `api.start`, `veronica.message`.
- **Installed app (owner 2026-10-14: "a real installed app: installer, Start Menu, uninstall, her own folder in
  AppData, updates like JARVIS"; "a separate VERONICA app until the merge"):** `paths.py` says where the program and
  her data are: in this coding folder both are here (as before); in an installed release the program is
  `%LOCALAPPDATA%\VERONICA\releases\<id>\app` (marked by `installed-release.json`) and her data (`.env`, settings,
  `data\`, `reports\`) is `%LOCALAPPDATA%\VERONICA`. Every file path goes through `paths`. `tools\build_release.py
  --version X` (clean tree only) makes `D:\VERONICA-release-work\VERONICA-Setup-X.exe` with Inno Setup 6: her code
  from HEAD, the JARVIS-pinned Python 3.12.10 embeddable plus her `.venv` packages, the speech model, and the launcher
  `launcher\VERONICA-installed.cs`. The Setup's step `installer.py install` switches `current.json`, keeps one
  previous release, removes older ones, and on the first install into the real home COPIES her data from this folder
  (never in tests or a sandbox home: `migrate_from` must be passed explicitly). Uninstall keeps her data. An update
  is the next version's Setup. Tests: `test_installed_app.py`. Installing is the owner's step (AppData).
- **Known test failures (2026-10-13):** 20 tests fail on the uncommitted working tree, the same 20 before and after
  the Qwen switch: the safety, approval-before-sending and marker-defusing tests (the rules they check were removed
  in the uncommitted `safety.py`/`agent.py` of 2026-10-12, which the owner said to leave as they are), and the speech
  model test in a throwaway copy (models\ is not copied).

- **One AI tool per project (owner, 2026-10-12; tool replaced 2026-10-13):** Hermes Agent (Nous Research) is the one
  tool that writes VERONICA ("I WANT TO REPLACE CLAUDE AND MAKE YOU THE ONLY CODER"; record
  `..\governance\HERMES-SOLE-CODER-APPROVAL-RECORD.json`). Other AI tools (Qwen Code, OpenClaw, ...) do not edit this
  folder.
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
