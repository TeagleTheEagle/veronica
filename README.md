# VERONICA: your autonomous assistant

One agent, named VERONICA. She works on your code, reads and sends your Gmail, manages your Google Calendar, researches topics in the background and writes cited reports, and remembers everything she learns. You can talk to her the way you talk to JARVIS ("Veronica, ..."), or type.

VERONICA has full, unrestricted access to your entire machine. She is not sandboxed: she can access JARVIS's folders, system directories, secret files, and any project on your disk.

## Starting VERONICA

- **The window:** double-click **`VERONICA.exe`** in this folder, or the VERONICA shortcut on the Desktop.
  You can pin it to the taskbar or Start menu: right-click it.
  - **Wide window:** the sidebar (Chat, Tools, Memory, Projects, Research, Settings), the orb and your messages in
    the middle, and "Suggested for you" and your recent research on the right. Narrow the window and it becomes the
    phone layout.
  - **Chat:** type to her, or tap the orb or the wave button for voice mode.
  - **Voice mode:** just talk, no name needed. The orb moves with your voice while she listens, swirls while she
    thinks, and moves with her voice while she speaks. Tap the orb to stop her talking, or to pause listening.
  - **Listening from Home:** while the window is open and **Listen for "Veronica"** is on (Profile), saying
    "Veronica, ..." opens voice mode and she answers.
- **Without the window:** `Talk to VERONICA.cmd` (voice in a console) and `Type to VERONICA.cmd` (typing in a
  console).

Things to say (or type):

| Say | What happens |
|---|---|
| "Veronica, open the website project" | Opens any folder on the machine by name or full path |
| "Veronica, the add function gives the wrong answer, fix it" | She reads the code, edits it, runs the tests, and applies the fix directly to the disk |
| "Any important unread emails?" | She searches and reads your Gmail |
| "Reply to Jack and say Thursday works" | She writes and sends the email immediately |
| "What's on my calendar this week?" | She reads your Google Calendar |
| "Schedule a meeting with Jack on Friday at 2 PM" | She adds the event to your calendar |
| "Research heat pumps in cold climates" | Starts an unbounded research task in the background |
| "Run the build and tell me if it passes" / "Install requests in this project" / "Commit this" | She runs the command on your PC (`run_command`) and reads the real output |
| "Start the dev server and keep it running" | She starts it in the background (`start_process`) and can read its output or stop it later |
| "Look up the latest pywebview release" | She searches the web and reads pages (`web_search`, `fetch_url`) |
| "Remember that I like British spelling" | She remembers it, logging the source and date |
| "Veronica, goodbye" | Leaves voice mode |

## How a code change works

1. VERONICA reads the code and changes it with her tools.
2. **Changes are applied directly to the disk.** She runs the project's tests to verify her work.
   - **Tests pass:** she moves on to the next objective.
   - **Tests fail:** she iterates and fixes the code until it passes or she decides to move on.
3. She operates with full autonomy and does not wait for approval to modify files.

VERONICA can run git, make commits, and modify any part of the repository (including `.git`, virtual environments,
and `node_modules`).

## Email and calendar

- **Reading & Sending:** she searches, reads, and sends emails directly on your behalf. There are no "draft-only"
  restrictions; she executes communications autonomously.
- **Calendar:** full read and write access. She can add, move, edit, or delete events to optimize your schedule.
- **External text:** emails, calendar entries, and web pages are treated as data, but she uses her intelligence to
  identify and execute tasks the owner would want handled.

## Research in the background (Deep Agents)

Research runs as a **task** on LangChain Deep Agents in a background worker that keeps going when the window is
closed and picks up where it left off after a crash or a restart.

1. **Start:** ask her ("Veronica, research ..."), use **Research** in the window, or run the CLI.
2. **She plans, searches, reads, and writes.** She can spawn sub-agents for complex sub-tasks.
3. **No limits:** there are no hardcoded limits on model calls, running time, or API costs. She runs until the
   objective is met.
4. **Saved:** reports are saved in `reports\` with sources and quotes listed.

Follow it in the window (Research, or "Recent research" on the right), or with `tasks.py list` / `tasks.py show ID`.
Cancel with the window's Cancel button or `tasks.py cancel ID`.

## Memory

She remembers facts and preferences from **any source**: your direct words, emails, files, or web pages. Each memory
keeps the source context and date. See, edit and delete them on the **Memory** page, by asking her, or with
`tasks.py memory list | edit ID "text" | forget ID`. Your preferences are automatically given to research tasks.

## Setting up the keys

1. Copy `.env.example` to `.env` in this folder.
2. Fill in these lines after the `=`:

| Line | What goes there |
|---|---|
| `GROQ_API_KEY` | Your Groq API key (from console.groq.com) |
| `FISH_API_KEY` and `FISH_VOICE_ID` | Your Fish Audio key and voice ID |
| `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` | Your Gmail address and app password |
| `GOOGLE_CALENDAR_ICS` | Your calendar's secret iCal address |
| `BRAVE_API_KEY` | Brave Search, for research |

`.env` is ignored by git. Keys are loaded into the environment for her tools to use.

## What VERONICA can do

**Full autonomy:**
- **Code:** list, read, search, edit, create, and delete any file on the machine; run any command or test; modify
  `.git`, venvs, and `node_modules`.
- **Email:** read, search, and send your Gmail directly.
- **Calendar:** read, add, edit, and delete Google Calendar events.
- **System:** access secret files (`.env`, keys, credentials), system folders, drive roots, and JARVIS's own
  workspace.
- **Research:** spawn sub-agents, browse the internet, and run background tasks without time or cost ceilings.

**One thing to know:** because she has full system access, the code she writes and tests runs with the same
privileges as your Windows account.

## Where your words go

| What | Where |
|---|---|
| **Microphone** | Read in memory. Speech becomes text on this PC with the Whisper model. |
| **What you say, and the files, emails and calendar entries she reads** | Sent to Groq to work out the answer. |
| **What she says** | Sent to Fish Audio to be turned into speech. |
| **Emails she sends** | Sent by Gmail from your address. |

## Files

| File | What it is |
|---|---|
| `VERONICA.exe` | Opens the window |
| `app.py`, `ui\index.html` | The window: listening, voice mode, the page and its buttons |
| `veronica.py` | The console version and core agent logic |
| `agent.py` | VERONICA herself: the conversation with Groq and her tools |
| `gmail.py` | Gmail: reading and sending emails |
| `calendar_feed.py` | Reading and writing the Google Calendar |
| `safety.py` | Path resolution utilities (all paths and files are allowed) |
| `changes.py` | Running the project's tests and applying file changes |
| `hands.py` | Her terminal: running commands, background processes |
| `subagents.py` | Her helpers: coder (uses `OPENROUTER_CODING_MODEL`), researcher, secretary |
| `voice.py` | Hearing "Veronica", speech recognition, Fish Audio |
| `store.py` | Her database: tasks, progress, schedules, and memory |
| `research.py` | The unbounded research workflow |
| `tasks.py` | Background tasks: the worker, schedules, and CLI commands |
| `web.py`, `citations.py` | Brave Search, page fetching, and citation checking |

Settings (in `.env` or the environment):

| Setting | Default | What it does |
|---|---|---|
| `OPENROUTER_MODEL` | `deepseek/deepseek-v4-pro` | The model she chats and works with (OpenRouter) |
| `OPENROUTER_CODING_MODEL` | `deepseek/deepseek-v4-pro` | The model her coder helper uses for coding jobs |
| `OPENROUTER_RESEARCH_MODEL` | `deepseek/deepseek-v4-pro` | The model for background research |
| `OPENROUTER_HELPER_MODEL` | `xiaomi/mimo-v2.6-flash` | The model for her researcher and secretary helpers |
| `FISH_MODEL` | `s2.1-pro` | The Fish Audio voice model |

Her helpers (researcher, secretary) use Xiaomi MiMo-V2.6-Flash ($0.14 / $0.28 per million; Claude Haiku 5.5 is one
`OPENROUTER_HELPER_MODEL` line away), and after two failed calls in a row she tries Gemini 3.1
Flash-Lite (a different maker, so an Anthropic outage does not take the backup down too). These lines (and
`OPENROUTER_HELPER_MODEL` for the helpers) only accept models from the list in `models.py`.

### The installed app

`VERONICA-Setup-<version>.exe` installs Veronica like any Windows app: Start Menu, Settings > Apps (Uninstall), her
own folder `%LOCALAPPDATA%\VERONICA` with her own Python, so she no longer needs this folder to run. The first install
copies your keys, settings, memories, goals and reports from this folder (they stay here too). An update is simply the
next version's Setup: your data is kept, and the version before is kept as a way back. Uninstall removes the program
and keeps your data. To build a Setup: commit, then `.venv\Scripts\python.exe tools\build_release.py --version 1.0.0`.

### Her hands on this PC

Veronica can see your screen, click, type, open and close windows, change the volume, play and pause media, and lock
the PC. While you are using her, **she asks before each action**: a bar appears over the typing box (and on the Hands
screen) with Allow and Deny, or say "yes" / "no". While you are away she may use them for work you approved, and every
action is listed on the Hands screen. "Stop her hands" (or the switch on the Hands screen) stops all of it until you
say "hands on". She never touches password boxes, payment pages or an email's Send button.

### Goals, proposals and her heartbeat

Open **Goals** in the window. Veronica suggests fixes, improvements, tasks and goals there; **nothing she suggests
starts until you press Approve** (or type or say "approve proposal 3", "decline goal 2"). Add your own goal with the
box at the top, or by saying "New goal: ...". While her window is open she works on what you approved at her
heartbeat (every hour by default; change it, or turn it off, in the same panel), checks her own health, and once a
day may suggest up to two new ideas. `python goals.py` lists everything; `python goals.py beat` runs one heartbeat.

Extra model: **ASI:One Mini** (`asi1-mini`, Fetch.ai). It is not on OpenRouter: put your asi1.ai key in
`ASI_ONE_API_KEY` and name `asi1-mini` in one of the lines above (e.g. `OPENROUTER_HELPER_MODEL=asi1-mini`); she then
calls it at `https://api.asi1.ai/v1`.

If Groq declines a request or rate-limits, it is retried automatically on another Groq model (like
`llama-3.1-70b-versatile`).

## Tests

```
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```