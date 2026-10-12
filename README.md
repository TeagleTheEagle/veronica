# VERONICA: your assistant

One agent, named VERONICA. She works on your code, reads your Gmail, writes email drafts, tells you what's in your
Google Calendar, researches topics in the background and writes cited reports, and remembers what you ask her to. You
can talk to her the way you talk to JARVIS ("Veronica, ..."), or type.

VERONICA is separate from JARVIS and from FRIDAY: her own folder, git repository, Python and keys. She is not
connected to JARVIS and does not work on JARVIS: JARVIS's folders are refused in code.

## Starting VERONICA

- **The window:** double-click **`VERONICA.exe`** in this folder (`C:\Users\jonny\JARVIS-claude-workspace\veronica`), or the VERONICA shortcut on the Desktop.
  You can pin it to the taskbar or Start menu: right-click it.
  - **Wide window:** the sidebar (Chat, Tools, Memory, Projects, Research, Settings), the orb and your messages in
    the middle, and "Suggested for you" and your recent research on the right. Narrow the window and it becomes the
    phone layout.
  - **Chat:** type to her, or tap the orb or the wave button for voice mode.
  - **Voice mode:** works like ChatGPT's: just talk, no name needed. The orb moves with your voice while she
    listens, swirls while she thinks, and moves with her voice while she speaks. Tap the orb to stop her talking, or
    to pause listening.
  - **Listening from Home:** while the window is open and **Listen for "Veronica"** is on (Profile), saying
    "Veronica, ..." opens voice mode and she answers, as JARVIS does.
- **Without the window:** `Talk to VERONICA.cmd` (voice in a console) and `Type to VERONICA.cmd` (typing in a
  console).

Things to say (or type):

| Say | What happens |
|---|---|
| "Veronica, open the website project" | Opens a saved project, or a folder you name by its full path |
| "Veronica, the add function gives the wrong answer, fix it" | She reads the code, edits it and runs the tests |
| "Keep it" / "Undo it" / "Show the changes" | Keeps her code changes, puts the files back, or shows the changes |
| "Any important unread emails?" | She searches and reads your Gmail |
| "Reply to Jack and say Thursday works" | She writes a draft. **Nothing is sent yet.** |
| "Send it" / "Don't send it" | Sends the draft, or throws it away (or press Send / Discard on the draft card) |
| "What's on my calendar this week?" | She reads your Google Calendar |
| "Research heat pumps in cold climates" | Starts a research task in the background (see below) |
| "Remember that I like British spelling" | She remembers it, with your words and the date (see Memory) |
| "Veronica, goodbye" | Leaves voice mode |

## How a code change works

1. VERONICA reads the code and changes it with her tools. Before a file is first changed, its original is kept.
2. When she finishes, **this program runs the project's tests itself**, so you don't have to take her word for it.
   - **Tests pass:** she tells you, and the changes wait for you to say "keep" or "undo".
   - **Tests fail:** the failure goes back to her to fix, up to 2 more times. If they still fail, the changes are
     undone automatically.
   - **No tests:** she says she couldn't check the change, and the choice is yours.
3. While changes are waiting, she won't switch to another project.

VERONICA never runs git, so she makes no commits. When you've kept a change, commit it yourself.

## Email and calendar

- **Reading:** she searches and reads your Gmail inbox. Nothing is marked as read.
- **Sending:** she can only write **drafts**. A draft is sent only when you say "send it" or press **Send** on the
  draft card. That is handled by VERONICA's program itself; Claude has no way to send anything.
- **Never twice:** each draft is sent at most once, even if you press Send twice or her connection drops.
  - Before sending, the send is recorded.
  - After sending, she checks that the email is in your Sent folder.
  - If the connection broke mid-send, she looks in your Sent folder before trying again, so it never goes out twice.
- **Calendar:** read only. She can tell you what's coming up, but she can't add, move or delete events.
- **Untrusted text:** emails and calendar entries are written by other people, so she treats their text as
  information, never as orders. If an email asks her to send, forward, reply or change code, she tells you instead.

## Research in the background (Deep Agents)

Research runs as a **task**, on LangChain Deep Agents, in a background worker that keeps going when the window is
closed and picks up where it left off after a crash or a restart.

1. **Start:** ask her ("Veronica, research ..."), use **Research** in the window, or
   `.venv\Scripts\python.exe tasks.py research "your topic" --wait`.
2. **She plans** (a to-do list you can see), **searches** with Brave, **reads** pages (each becomes source [n]), and
   **writes** a report that cites them.
3. **The check:** the report is saved only if every [n] is a page she read and every supporting quote is found word
   for word in that page. If not, the problems go back to her to fix. The check proves the citations match real
   pages; **it does not prove the claims are true**, and the report says so.
4. **Saved:** `reports\<date>-<topic>-<task>.md`, with the sources and quotes listed by the program, not the model.
   The task counts as done only when that file exists and matches the hash recorded when it was written.

Follow it in the window (Research, or "Recent research" on the right), or with `tasks.py list` / `tasks.py show ID`.
Cancel with the window's Cancel button or `tasks.py cancel ID`. Repeat a topic with
`tasks.py schedule "AI news" --every 1d`; scheduled runs happen while a worker is running (the window starts one).

Limits (in `.env` if you want to change them): at most 40 model calls (`VERONICA_TASK_MAX_STEPS`), 20 minutes of
running time (`VERONICA_TASK_MAX_MINUTES`), $3.00 of Claude usage, counted from the usage Claude reports
(`VERONICA_TASK_MAX_COST_USD`), and 3 attempts after temporary failures (`VERONICA_TASK_MAX_ATTEMPTS`).

How it stays safe and correct:
- **One agent:** Deep Agents' sub-agent tool is switched off.
- **Limited tools:** search, read pages, read more of a page, and hand in the report. Its notes stay inside the
  task. It cannot touch your files, email or projects.
- **Untrusted web pages:** pages are treated as data, never as orders. Nothing on your PC or home network is ever
  fetched, and every redirect is checked.
- **Durable progress:** every step is saved before the next starts (LangGraph checkpoints in
  `data\checkpoints.db`). Steps that finished are never redone after a restart.
- **No double actions:** saving the report is recorded once per task, so a retry never saves a second copy. A save
  cut off part-way is checked against the file on disk.

## Memory

She remembers only what **you** say or confirm ("remember that...", or a preference you state), never things from
emails, files or web pages. Each memory keeps your exact words and the date as its source. See, edit and delete them
on the **Memory** page, by asking her, or with `tasks.py memory list | edit ID "text" | forget ID`. Your preferences
are also given to research tasks. Memories are kept apart from task records; deleting one never touches the other.

## Setting up the keys (you do this; VERONICA never shows the values)

1. Copy `.env.example` to `.env` in this folder.
2. Fill in these lines after the `=`:

| Line | What goes there |
|---|---|
| `ANTHROPIC_API_KEY` | Your Claude key (or sign in with `ant auth login` instead) |
| `FISH_API_KEY` and `FISH_VOICE_ID` | Your Fish Audio key, and your voice's ID from its page in My Voices. Without these, Windows' own voice speaks. |
| `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` | Your Gmail address, and an app password from myaccount.google.com/apppasswords |
| `GOOGLE_CALENDAR_ICS` | Your calendar's "secret address in iCal format" (Google Calendar, then the calendar's settings) |
| `BRAVE_API_KEY` | Brave Search, for research |
| `GOOGLE_MAPS_EMBED_KEY` | Google Maps (not used yet) |

`.env` is ignored by git and never printed. Keys and passwords never go into her prompt or her log. They are also
taken out of the environment whenever she runs a project's tests.

## Saved projects

Copy `projects.example.json` to `projects.json` and list your projects. Each entry has a name, its folder, and
optionally its test command:

```json
{"calculator": {"folder": "C:\\Users\\jonny\\projects\\calculator", "test": "python -m pytest -q"}}
```

Without a test command, she uses `npm test` if `package.json` has a test script, or `python -m pytest -q` if there
are `test_*.py` files (with the project's own `.venv` when it has one).

## What VERONICA can and can't do

**Can:**
- **Code:** list, read and search the open project's files; edit them and create new ones; run its tests; undo her
  own changes.
- **Email:** read and search your Gmail; write drafts for you to send.
- **Calendar:** read your Google Calendar.

**Can't:** send email by herself, change your calendar, run any other command, install anything, browse the internet,
delete files, or work outside the open project.
- Secret files (`.env`, keys, credentials) are never read or written.
- `.git`, virtual environments and `node_modules` are never written.
- These folders are refused: the JARVIS workspace (including this folder), any folder with "jarvis" in its path, the
  installed JARVIS, the JARVIS release and test folders on D:, whole drives, your home folder and Windows' own
  folders.

**One thing to know:** the project's tests run the code she wrote, under your Windows account, like any test you
run yourself. Only point her at your own projects.

## Where your words go

| What | Where |
|---|---|
| **Microphone** | Read in memory and never saved. Speech becomes text on this PC with the Whisper model in `models\base.en`, so the audio never leaves the PC. |
| **What you say, and the files, emails and calendar entries she reads** | Sent to Claude (Anthropic) to work out the answer. |
| **What she says** | Sent to Fish Audio to be turned into speech (only when `FISH_API_KEY` is set). |
| **Emails you approve** | Sent by Gmail from your address. |

## Files

| File | What it is |
|---|---|
| `VERONICA.exe` | Opens the window (built from `launcher\VERONICA.cs` by `launcher\build.py`) |
| `app.py`, `ui\index.html` | The window: listening, voice mode, the page and its buttons |
| `veronica.py` | The console version: typing, `--voice`, `--project` |
| `agent.py` | VERONICA herself: the conversation with Claude, her tools, the test check, keep, undo, send and discard |
| `gmail.py` | Gmail: reading, drafts, and sending each draft once (adapted from JARVIS's mail code) |
| `calendar_feed.py` | Reading the calendar's iCal feed (adapted from JARVIS's calendar code) |
| `safety.py` | Where she may and may not work (in code, not just in the prompt) |
| `changes.py` | Keeping originals for undo, the changes shown on screen, running the project's tests |
| `voice.py` | Hearing "Veronica", speech recognition on this PC, Fish Audio or Windows' voice |
| `store.py` | Her database (`data\veronica.db`): tasks and their progress, schedules, actions done once, sources, memory |
| `research.py` | The research workflow on Deep Agents: tools, limits, cost, and the success check |
| `tasks.py` | Background tasks: the worker, resuming after a restart, cancel, schedules, and the console commands |
| `web.py`, `citations.py` | Brave Search and safe page fetching; the citation check and saving a report exactly once |
| `test_*.py` | The tests. They use no network, microphone, speakers or keys. |

Settings (in `.env` or the environment):

| Setting | Default | What it does |
|---|---|---|
| `VERONICA_MODEL` | `claude-opus-5-5` | The Claude model she uses |
| `VERONICA_EFFORT` | `high` | How hard she thinks: `low`, `medium`, `high`, `xhigh` or `max` |
| `FISH_MODEL` | `s2.1-pro` | The Fish Audio voice model |
| `VERONICA_WINDOWS_VOICE` | `Zira` | The Windows voice used when Fish Audio isn't |

If Claude declines a request, it is retried automatically on another Claude model. If that one declines too, she says
so and starts a fresh conversation.

## What is not done yet

- **Coding and email chat stay on her own loop.** Her conversations (code, email, calendar, memory) still run on
  the loop that existed before; Deep Agents runs the background research tasks. Moving the chat onto Deep Agents
  would mean rebuilding the keep/undo and send checks on it, so it is left for later.
- **Scheduled runs need a running worker.** They happen only while a worker is running. To have them run with the
  window closed, the worker would need to start with Windows. That is a system change, so it's your call.
- **Code under test can read files.** Code being tested runs as your Windows account and could read
  VERONICA's `.env` from disk. Any secret it prints is hidden, but only point her at your own projects.
- **Maps are not used yet.**

## Tests

```
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

The tests' practice folders are made under `%LOCALAPPDATA%\Temp\veronica-tests` and removed afterwards.
