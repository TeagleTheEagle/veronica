"""VERONICA's durable state, in one SQLite file (data/veronica.db):

- tasks and their events: what a background task is, its status, progress, result, error, and the LangGraph thread it
  runs in (thread_id), so a task that was running when the PC or the worker stopped is picked up again and resumed;
- schedules: recurring runs (a research topic every day, say);
- operations: one row per external action, keyed by a stable operation id, so an action is never done twice and an
  action whose outcome is uncertain is checked before it is tried again;
- sources: the pages a research task actually retrieved (their text and a hash), the only things a report may cite;
- memories: what the owner asked VERONICA to remember, with where it came from and when. Memory is separate from task
  state: deleting tasks never deletes memories, and the other way round.

Where a running task is inside its plan (LangGraph's checkpoints) is kept by LangGraph in data/checkpoints.db.
Every change is one SQLite transaction; claiming work uses BEGIN IMMEDIATE so two workers never take the same task.
"""
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import safety

DATA = safety.HERE / 'data'
DB_PATH = DATA / 'veronica.db'
CHECKPOINTS_PATH = DATA / 'checkpoints.db'

FINISHED = ('succeeded', 'failed', 'cancelled')
STATUSES = ('queued', 'running') + FINISHED

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    input TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    progress TEXT NOT NULL DEFAULT '{}',
    result TEXT,
    error TEXT,
    thread_id TEXT NOT NULL,
    request_id TEXT UNIQUE,
    schedule_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_until REAL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks (status, created_at);
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    at REAL NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS task_events_task ON task_events (task_id, id);
CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    input TEXT NOT NULL,
    every_seconds INTEGER NOT NULL,
    next_run_at REAL NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS operations (
    op_id TEXT PRIMARY KEY,
    task_id TEXT,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    intent TEXT NOT NULL,
    status TEXT NOT NULL,
    result TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    task_id TEXT NOT NULL,
    n INTEGER NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    PRIMARY KEY (task_id, n),
    UNIQUE (task_id, url)
);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    provenance TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workers (
    id TEXT PRIMARY KEY,
    pid INTEGER NOT NULL,
    beat_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    origin TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '[]',
    outcome TEXT,
    beats INTEGER NOT NULL DEFAULT 0,
    worked_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    decided_at REAL
);
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    goal_id INTEGER,
    status TEXT NOT NULL,
    outcome TEXT,
    beats INTEGER NOT NULL DEFAULT 0,
    worked_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    decided_at REAL
);
CREATE TABLE IF NOT EXISTS heartbeats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    kind TEXT NOT NULL,
    item TEXT,
    summary TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 1
);
"""

# Goals: 'proposed' (by VERONICA, waiting for the owner) -> 'active' (approved, or added by the owner) -> 'done';
# or 'declined' by the owner, 'paused' / 'dropped' by the owner. Proposals (a fix, an improvement, a task):
# 'pending' -> 'approved' -> 'done' or 'failed'; or 'declined'. Only the owner's own actions (the window's buttons,
# his typed or spoken "approve proposal 3") move anything out of 'proposed' / 'pending': VERONICA has no tool for it.
GOAL_STATUSES = ('proposed', 'active', 'paused', 'done', 'declined', 'dropped')
PROPOSAL_KINDS = ('fix', 'improvement', 'task')
PROPOSAL_STATUSES = ('pending', 'approved', 'done', 'failed', 'declined')


def now():
    return time.time()


def _row(row, *json_fields):
    if row is None:
        return None
    item = dict(row)
    for name in json_fields:
        if item.get(name) is not None:
            item[name] = json.loads(item[name])
    return item


class Store:
    def __init__(self, path=DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute('PRAGMA journal_mode=WAL')
            self.conn.execute('PRAGMA busy_timeout=30000')
            self.conn.executescript(SCHEMA)

    def close(self):
        with self.lock:
            self.conn.close()

    def _tx(self, immediate=False):
        return _Transaction(self, immediate)

    # ----- tasks -----------------------------------------------------------------------------------------------

    def create_task(self, kind, title, task_input, request_id=None, schedule_id=None):
        """A queued task. With a request_id, asking twice gives back the same task (no duplicate)."""
        with self._tx(immediate=True) as db:
            if request_id:
                found = db.execute('SELECT id FROM tasks WHERE request_id = ?', (request_id,)).fetchone()
                if found:
                    return self.get_task(found['id'])
            task_id = uuid.uuid4().hex[:12]
            stamp = now()
            db.execute('INSERT INTO tasks (id, kind, title, input, thread_id, request_id, schedule_id, created_at, '
                       'updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                       (task_id, kind, title, json.dumps(task_input), f'task-{task_id}', request_id, schedule_id,
                        stamp, stamp))
            db.execute('INSERT INTO task_events (task_id, at, kind, message) VALUES (?, ?, ?, ?)',
                       (task_id, stamp, 'queued', f'Queued: {title}'))
        return self.get_task(task_id)

    def get_task(self, task_id):
        with self.lock:
            row = self.conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
        return _row(row, 'input', 'progress', 'result')

    def list_tasks(self, limit=20, status=None):
        with self.lock:
            if status:
                rows = self.conn.execute('SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?',
                                         (status, limit)).fetchall()
            else:
                rows = self.conn.execute('SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?', (limit,)).fetchall()
        return [_row(r, 'input', 'progress', 'result') for r in rows]

    def claim_task(self, worker_id, lease_seconds=120):
        """The next task to run: a queued one, or a running one whose worker stopped renewing its lease (it crashed
        or the PC restarted). Returns None when there is nothing to do."""
        with self._tx(immediate=True) as db:
            stamp = now()
            row = db.execute("SELECT id FROM tasks WHERE status = 'queued' OR (status = 'running' AND "
                             "(lease_until IS NULL OR lease_until < ?)) ORDER BY created_at LIMIT 1",
                             (stamp,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE tasks SET status = 'running', lease_owner = ?, lease_until = ?, attempts = attempts + 1, "
                       "started_at = COALESCE(started_at, ?), updated_at = ? WHERE id = ?",
                       (worker_id, stamp + lease_seconds, stamp, stamp, row['id']))
        return self.get_task(row['id'])

    def renew_lease(self, task_id, worker_id, lease_seconds=120):
        with self._tx() as db:
            changed = db.execute("UPDATE tasks SET lease_until = ?, updated_at = ? WHERE id = ? AND lease_owner = ? "
                                 "AND status = 'running'", (now() + lease_seconds, now(), task_id, worker_id)).rowcount
        return changed == 1

    def update_progress(self, task_id, progress=None, tokens_in=0, tokens_out=0, cost_usd=0.0):
        with self._tx() as db:
            if progress is not None:
                db.execute('UPDATE tasks SET progress = ?, updated_at = ? WHERE id = ?',
                           (json.dumps(progress), now(), task_id))
            if tokens_in or tokens_out or cost_usd:
                db.execute('UPDATE tasks SET tokens_in = tokens_in + ?, tokens_out = tokens_out + ?, '
                           'cost_usd = cost_usd + ?, updated_at = ? WHERE id = ?',
                           (tokens_in, tokens_out, cost_usd, now(), task_id))

    def add_event(self, task_id, kind, message):
        with self._tx() as db:
            db.execute('INSERT INTO task_events (task_id, at, kind, message) VALUES (?, ?, ?, ?)',
                       (task_id, now(), kind, str(message)[:2000]))

    def events(self, task_id, after_id=0, limit=200):
        with self.lock:
            rows = self.conn.execute('SELECT * FROM task_events WHERE task_id = ? AND id > ? ORDER BY id LIMIT ?',
                                     (task_id, after_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def finish_task(self, task_id, status, result=None, error=None, worker_id=None):
        """Mark a task finished. With worker_id, only the worker that holds the task may finish it."""
        assert status in FINISHED
        with self._tx() as db:
            query = ('UPDATE tasks SET status = ?, result = ?, error = ?, finished_at = ?, updated_at = ?, '
                     'lease_owner = NULL, lease_until = NULL WHERE id = ?')
            params = [status, json.dumps(result) if result is not None else None, error, now(), now(), task_id]
            if worker_id is not None:
                query += ' AND lease_owner = ?'
                params.append(worker_id)
            changed = db.execute(query, params).rowcount
            if changed:
                db.execute('INSERT INTO task_events (task_id, at, kind, message) VALUES (?, ?, ?, ?)',
                           (task_id, now(), status, error or status.capitalize()))
        return changed == 1

    def request_cancel(self, task_id):
        """Cancel a task: a queued one at once; a running one at its next step. Returns the task."""
        with self._tx(immediate=True) as db:
            row = db.execute('SELECT status FROM tasks WHERE id = ?', (task_id,)).fetchone()
            if row is None:
                return None
            if row['status'] == 'queued':
                db.execute("UPDATE tasks SET status = 'cancelled', cancel_requested = 1, finished_at = ?, "
                           "updated_at = ? WHERE id = ?", (now(), now(), task_id))
                db.execute('INSERT INTO task_events (task_id, at, kind, message) VALUES (?, ?, ?, ?)',
                           (task_id, now(), 'cancelled', 'Cancelled before it started'))
            elif row['status'] == 'running':
                db.execute('UPDATE tasks SET cancel_requested = 1, updated_at = ? WHERE id = ?', (now(), task_id))
                db.execute('INSERT INTO task_events (task_id, at, kind, message) VALUES (?, ?, ?, ?)',
                           (task_id, now(), 'cancelling', 'Cancel requested; stopping at the next step'))
        return self.get_task(task_id)

    def cancel_requested(self, task_id):
        with self.lock:
            row = self.conn.execute('SELECT cancel_requested FROM tasks WHERE id = ?', (task_id,)).fetchone()
        return bool(row and row['cancel_requested'])

    # ----- schedules -------------------------------------------------------------------------------------------

    def add_schedule(self, kind, title, task_input, every_seconds, first_run_at=None):
        if every_seconds < 60:
            raise ValueError('A schedule repeats at most once a minute.')
        schedule_id = uuid.uuid4().hex[:10]
        with self._tx() as db:
            db.execute('INSERT INTO schedules (id, kind, title, input, every_seconds, next_run_at, created_at) '
                       'VALUES (?, ?, ?, ?, ?, ?, ?)', (schedule_id, kind, title, json.dumps(task_input),
                                                       int(every_seconds), first_run_at or now(), now()))
        return self.get_schedule(schedule_id)

    def get_schedule(self, schedule_id):
        with self.lock:
            row = self.conn.execute('SELECT * FROM schedules WHERE id = ?', (schedule_id,)).fetchone()
        return _row(row, 'input')

    def list_schedules(self):
        with self.lock:
            rows = self.conn.execute('SELECT * FROM schedules ORDER BY created_at').fetchall()
        return [_row(r, 'input') for r in rows]

    def set_schedule_enabled(self, schedule_id, enabled):
        with self._tx() as db:
            return db.execute('UPDATE schedules SET enabled = ? WHERE id = ?',
                              (1 if enabled else 0, schedule_id)).rowcount == 1

    def delete_schedule(self, schedule_id):
        with self._tx() as db:
            return db.execute('DELETE FROM schedules WHERE id = ?', (schedule_id,)).rowcount == 1

    def enqueue_due(self, at=None):
        """Queue one task for every schedule that is due. Each run has a request id made of the schedule and its
        due time, so two workers (or a restart) never queue the same run twice. Missed runs are not piled up: the
        next run moves past now."""
        stamp = at or now()
        queued = []
        with self._tx(immediate=True) as db:
            due = db.execute('SELECT * FROM schedules WHERE enabled = 1 AND next_run_at <= ?', (stamp,)).fetchall()
            for schedule in due:
                request_id = f'schedule:{schedule["id"]}:{int(schedule["next_run_at"])}'
                if not db.execute('SELECT 1 FROM tasks WHERE request_id = ?', (request_id,)).fetchone():
                    task_id = uuid.uuid4().hex[:12]
                    db.execute('INSERT INTO tasks (id, kind, title, input, thread_id, request_id, schedule_id, '
                               'created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                               (task_id, schedule['kind'], schedule['title'], schedule['input'], f'task-{task_id}',
                                request_id, schedule['id'], stamp, stamp))
                    db.execute('INSERT INTO task_events (task_id, at, kind, message) VALUES (?, ?, ?, ?)',
                               (task_id, stamp, 'queued', f'Queued by schedule {schedule["id"]}'))
                    queued.append(task_id)
                every = schedule['every_seconds']
                missed = int((stamp - schedule['next_run_at']) // every) + 1
                db.execute('UPDATE schedules SET next_run_at = ? WHERE id = ?',
                           (schedule['next_run_at'] + missed * every, schedule['id']))
        return queued

    # ----- operations (external actions, done once) -------------------------------------------------------------

    def begin_operation(self, op_id, task_id, kind, target, intent):
        """Record the intent to do an action before doing it. Returns (row, created): when the row already exists
        (status pending, done, failed or uncertain) the caller must not simply do the action again."""
        with self._tx(immediate=True) as db:
            row = db.execute('SELECT * FROM operations WHERE op_id = ?', (op_id,)).fetchone()
            if row is not None:
                return _row(row, 'intent', 'result'), False
            db.execute('INSERT INTO operations (op_id, task_id, kind, target, intent, status, created_at, updated_at) '
                       "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                       (op_id, task_id, kind, target, json.dumps(intent), now(), now()))
        return self.get_operation(op_id), True

    def finish_operation(self, op_id, status, result=None):
        assert status in ('done', 'failed', 'uncertain', 'pending')
        with self._tx() as db:
            db.execute('UPDATE operations SET status = ?, result = ?, updated_at = ? WHERE op_id = ?',
                       (status, json.dumps(result) if result is not None else None, now(), op_id))
        return self.get_operation(op_id)

    def operations(self, kind, statuses):
        with self.lock:
            rows = self.conn.execute(f'SELECT * FROM operations WHERE kind = ? AND status IN '
                                     f'({",".join("?" * len(statuses))}) ORDER BY created_at',
                                     (kind, *statuses)).fetchall()
        return [_row(r, 'intent', 'result') for r in rows]

    def get_operation(self, op_id):
        with self.lock:
            row = self.conn.execute('SELECT * FROM operations WHERE op_id = ?', (op_id,)).fetchone()
        return _row(row, 'intent', 'result')

    # ----- sources (what a research task retrieved) -------------------------------------------------------------

    def add_source(self, task_id, url, title, text, content_hash):
        """The source's number in this task. The same URL fetched twice keeps its first number."""
        with self._tx(immediate=True) as db:
            row = db.execute('SELECT n FROM sources WHERE task_id = ? AND url = ?', (task_id, url)).fetchone()
            if row:
                return row['n'], False
            n = db.execute('SELECT COALESCE(MAX(n), 0) + 1 AS n FROM sources WHERE task_id = ?',
                           (task_id,)).fetchone()['n']
            db.execute('INSERT INTO sources (task_id, n, url, title, text, content_hash, fetched_at) '
                       'VALUES (?, ?, ?, ?, ?, ?, ?)', (task_id, n, url, title, text, content_hash, now()))
        return n, True

    def sources(self, task_id):
        with self.lock:
            rows = self.conn.execute('SELECT * FROM sources WHERE task_id = ? ORDER BY n', (task_id,)).fetchall()
        return [dict(r) for r in rows]

    def source(self, task_id, n):
        with self.lock:
            row = self.conn.execute('SELECT * FROM sources WHERE task_id = ? AND n = ?', (task_id, n)).fetchone()
        return dict(row) if row else None

    # ----- memories --------------------------------------------------------------------------------------------

    def remember(self, kind, content, provenance):
        with self._tx() as db:
            cursor = db.execute('INSERT INTO memories (kind, content, provenance, created_at, updated_at) '
                                'VALUES (?, ?, ?, ?, ?)', (kind, content, provenance, now(), now()))
        return self.memory(cursor.lastrowid)

    def update_memory(self, memory_id, content, provenance):
        with self._tx() as db:
            changed = db.execute('UPDATE memories SET content = ?, provenance = ?, updated_at = ? WHERE id = ?',
                                 (content, provenance, now(), memory_id)).rowcount
        return self.memory(memory_id) if changed else None

    def forget(self, memory_id):
        with self._tx() as db:
            return db.execute('DELETE FROM memories WHERE id = ?', (memory_id,)).rowcount == 1

    def memory(self, memory_id):
        with self.lock:
            row = self.conn.execute('SELECT * FROM memories WHERE id = ?', (memory_id,)).fetchone()
        return dict(row) if row else None

    def memories(self):
        with self.lock:
            rows = self.conn.execute('SELECT * FROM memories ORDER BY id').fetchall()
        return [dict(r) for r in rows]

    # ----- workers ---------------------------------------------------------------------------------------------

    def beat(self, worker_id, pid):
        with self._tx() as db:
            db.execute('INSERT INTO workers (id, pid, beat_at) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET '
                       'beat_at = excluded.beat_at, pid = excluded.pid', (worker_id, pid, now()))

    def worker_gone(self, worker_id):
        with self._tx() as db:
            db.execute('DELETE FROM workers WHERE id = ?', (worker_id,))

    def live_workers(self, within=45):
        with self.lock:
            rows = self.conn.execute('SELECT * FROM workers WHERE beat_at > ?', (now() - within,)).fetchall()
        return [dict(r) for r in rows]

    # ----- goals and proposals (stage 3) ------------------------------------------------------------------------

    def add_goal(self, title, detail='', origin='veronica'):
        """A goal. VERONICA's start as 'proposed' and wait for the owner; the owner's are 'active' at once. The same
        title already proposed or active gives back that goal (no duplicates)."""
        title, detail = _clean(title, 200), _clean(detail, 2000)
        if len(title) < 3:
            raise ValueError('Give the goal a title of at least 3 characters.')
        status = 'active' if origin == 'owner' else 'proposed'
        with self._tx(immediate=True) as db:
            row = db.execute("SELECT id FROM goals WHERE lower(title) = lower(?) AND status IN ('proposed', 'active', "
                             "'paused')", (title,)).fetchone()
            if row:
                return self.goal(row['id']), False
            cursor = db.execute('INSERT INTO goals (title, detail, status, origin, created_at, updated_at, decided_at) '
                                'VALUES (?, ?, ?, ?, ?, ?, ?)', (title, detail, status, origin, now(), now(),
                                                                 now() if origin == 'owner' else None))
        return self.goal(cursor.lastrowid), True

    def goal(self, goal_id):
        with self.lock:
            row = self.conn.execute('SELECT * FROM goals WHERE id = ?', (goal_id,)).fetchone()
        return _row(row, 'notes')

    def goals(self, statuses=None):
        with self.lock:
            if statuses:
                rows = self.conn.execute(f'SELECT * FROM goals WHERE status IN ({",".join("?" * len(statuses))}) '
                                         'ORDER BY id', tuple(statuses)).fetchall()
            else:
                rows = self.conn.execute('SELECT * FROM goals ORDER BY id').fetchall()
        return [_row(r, 'notes') for r in rows]

    def set_goal_status(self, goal_id, status, outcome=None, allowed_from=None):
        """Move a goal to `status`, only from one of `allowed_from` when given. The goal, or None when it was not
        there or not in an allowed status."""
        assert status in GOAL_STATUSES
        with self._tx(immediate=True) as db:
            row = db.execute('SELECT status FROM goals WHERE id = ?', (goal_id,)).fetchone()
            if row is None or (allowed_from and row['status'] not in allowed_from):
                return None
            decided = now() if row['status'] == 'proposed' and status != 'proposed' else None
            db.execute('UPDATE goals SET status = ?, outcome = COALESCE(?, outcome), updated_at = ?, '
                       'decided_at = COALESCE(?, decided_at) WHERE id = ?',
                       (status, _clean(outcome, 1000) if outcome else None, now(), decided, goal_id))
        return self.goal(goal_id)

    def note_goal(self, goal_id, note, worked=True):
        """Add a progress note to an active goal (the last 30 are kept). The goal, or None."""
        with self._tx(immediate=True) as db:
            row = db.execute('SELECT notes, status FROM goals WHERE id = ?', (goal_id,)).fetchone()
            if row is None:
                return None
            notes = json.loads(row['notes'])[-29:] + [{'at': now(), 'note': _clean(note, 1000)}]
            db.execute('UPDATE goals SET notes = ?, updated_at = ?' + (', worked_at = ?' if worked else '') +
                       ' WHERE id = ?', (json.dumps(notes), now(), *((now(),) if worked else ()), goal_id))
        return self.goal(goal_id)

    def add_proposal(self, kind, title, detail='', goal_id=None):
        """A fix, improvement or task VERONICA suggests; it waits ('pending') for the owner. The same title already
        pending or approved gives back that proposal."""
        kind = str(kind or '').strip().lower()
        if kind not in PROPOSAL_KINDS:
            raise ValueError(f'The kind is one of: {", ".join(PROPOSAL_KINDS)}.')
        title, detail = _clean(title, 200), _clean(detail, 3000)
        if len(title) < 3:
            raise ValueError('Give the proposal a title of at least 3 characters.')
        with self._tx(immediate=True) as db:
            row = db.execute("SELECT id FROM proposals WHERE lower(title) = lower(?) AND status IN ('pending', "
                             "'approved')", (title,)).fetchone()
            if row:
                return self.proposal(row['id']), False
            cursor = db.execute("INSERT INTO proposals (kind, title, detail, goal_id, status, created_at, updated_at) "
                                "VALUES (?, ?, ?, ?, 'pending', ?, ?)", (kind, title, detail, goal_id, now(), now()))
        return self.proposal(cursor.lastrowid), True

    def proposal(self, proposal_id):
        with self.lock:
            row = self.conn.execute('SELECT * FROM proposals WHERE id = ?', (proposal_id,)).fetchone()
        return dict(row) if row else None

    def proposals(self, statuses=None):
        with self.lock:
            if statuses:
                rows = self.conn.execute(f'SELECT * FROM proposals WHERE status IN '
                                         f'({",".join("?" * len(statuses))}) ORDER BY id', tuple(statuses)).fetchall()
            else:
                rows = self.conn.execute('SELECT * FROM proposals ORDER BY id').fetchall()
        return [dict(r) for r in rows]

    def set_proposal_status(self, proposal_id, status, outcome=None, allowed_from=None):
        assert status in PROPOSAL_STATUSES
        with self._tx(immediate=True) as db:
            row = db.execute('SELECT status FROM proposals WHERE id = ?', (proposal_id,)).fetchone()
            if row is None or (allowed_from and row['status'] not in allowed_from):
                return None
            decided = now() if row['status'] == 'pending' and status != 'pending' else None
            db.execute('UPDATE proposals SET status = ?, outcome = COALESCE(?, outcome), updated_at = ?, '
                       'decided_at = COALESCE(?, decided_at) WHERE id = ?',
                       (status, _clean(outcome, 1000) if outcome else None, now(), decided, proposal_id))
        return self.proposal(proposal_id)

    def worked_on(self, kind, item_id):
        """A heartbeat spent time on this goal or proposal."""
        table = {'goal': 'goals', 'proposal': 'proposals'}[kind]
        with self._tx() as db:
            db.execute(f'UPDATE {table} SET beats = beats + 1, worked_at = ?, updated_at = ? WHERE id = ?',
                       (now(), now(), item_id))

    def next_work(self):
        """What the heartbeat works on next: an approved proposal (the least recently worked on first), else an
        active goal (likewise). ('proposal' | 'goal', row) or None."""
        with self.lock:
            row = self.conn.execute("SELECT * FROM proposals WHERE status = 'approved' "
                                    "ORDER BY COALESCE(worked_at, 0), id LIMIT 1").fetchone()
            if row:
                return 'proposal', dict(row)
            row = self.conn.execute("SELECT * FROM goals WHERE status = 'active' "
                                    "ORDER BY COALESCE(worked_at, 0), id LIMIT 1").fetchone()
        return ('goal', _row(row, 'notes')) if row else None

    # ----- heartbeats ------------------------------------------------------------------------------------------

    def add_heartbeat(self, kind, item=None, summary='', ok=True):
        with self._tx() as db:
            cursor = db.execute('INSERT INTO heartbeats (at, kind, item, summary, ok) VALUES (?, ?, ?, ?, ?)',
                                (now(), kind, item, _clean(summary, 2000), 1 if ok else 0))
        return cursor.lastrowid

    def heartbeats(self, limit=20, kind=None):
        with self.lock:
            if kind:
                rows = self.conn.execute('SELECT * FROM heartbeats WHERE kind = ? ORDER BY id DESC LIMIT ?',
                                         (kind, limit)).fetchall()
            else:
                rows = self.conn.execute('SELECT * FROM heartbeats ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        return [dict(r) for r in rows]


def _clean(text, limit):
    return ' '.join(str(text or '').split())[:limit]


class _Transaction:
    def __init__(self, store, immediate):
        self.store, self.immediate = store, immediate

    def __enter__(self):
        self.store.lock.acquire()
        self.store.conn.execute('BEGIN IMMEDIATE' if self.immediate else 'BEGIN')
        return self.store.conn

    def __exit__(self, kind, value, trace):
        try:
            self.store.conn.execute('COMMIT' if kind is None else 'ROLLBACK')
        finally:
            self.store.lock.release()
        return False
