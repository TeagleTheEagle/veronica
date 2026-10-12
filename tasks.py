"""VERONICA's background tasks: a small persistent worker and scheduler on her SQLite store (store.py), running the
research workflow (research.py) durably.

- Start: a task row (queued) with its own LangGraph thread. A request id makes a double click harmless.
- The worker (`tasks.py worker`; the window and the chat start one when needed) claims one task at a time with a
  lease it renews every 20 seconds. If a worker stops (crash, PC restart), its task is claimed again when the lease
  runs out, and the run RESUMES from its last checkpoint (invoke(None) on the same thread, durability "sync"): steps
  that finished are not done again; a step that was cut off is done again, which is why every external action has
  its own operation record (citations.save_report).
- Cancel: a queued task is cancelled at once; a running one stops at its next step (the lease thread drains the run,
  and the guard checks before every model call).
- Schedules: recurring runs, queued by the worker when they are due, each run once (store.enqueue_due). They run
  while a worker is running.
- Success is decided from evidence (research.evidence: a saved report whose citation check passed and whose file
  matches the hash recorded when it was written), never from the agent's last words.
- Transient failures (connection, rate limit, Claude busy) put the task back in the queue to resume, up to
  MAX_ATTEMPTS times in all; anything else fails the task with the reason.

    python tasks.py research "heat pumps in cold climates" [--focus "..."] [--wait]
    python tasks.py list | show ID | cancel ID
    python tasks.py schedule "AI news this week" --every 1d | schedules | unschedule ID
    python tasks.py worker [--once]
    python tasks.py memory list | memory add "text" [--kind fact] | memory edit ID "text" | memory forget ID
"""
import argparse
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid

import store as store_mod

log = logging.getLogger('veronica.tasks')

LEASE_SECONDS = 120
RENEW_EVERY = 20
MAX_ATTEMPTS = int(os.environ.get('VERONICA_TASK_MAX_ATTEMPTS', '3'))
IDLE_EXIT = 600            # a worker with nothing to do and no schedules stops after ten minutes
POLL = 2.0


# ----- starting, inspecting, cancelling ----------------------------------------------------------------------------

def start_research(db, topic, focus=None, request_id=None, schedule_id=None):
    topic = ' '.join(str(topic or '').split())
    if not 3 <= len(topic) <= 500:
        raise ValueError('Give a research topic of 3 to 500 characters.')
    preferences = [m['content'] for m in db.memories() if m['kind'] == 'preference']
    task_input = {'topic': topic, 'focus': (focus or '').strip()[:500] or None, 'preferences': preferences}
    return db.create_task('research', topic[:120], task_input, request_id=request_id, schedule_id=schedule_id)


def describe(task, events=None):
    """A readable account of a task, for the console and the chat."""
    progress = task.get('progress') or {}
    lines = [f'{task["id"]}  {task["status"].upper()}  {task["title"]}']
    detail = []
    if progress.get('model_calls'):
        detail.append(f'{progress["model_calls"]} steps')
    if task.get('cost_usd'):
        detail.append(f'${task["cost_usd"]:.2f}')
    if progress.get('last_step') and task['status'] == 'running':
        detail.append(f'now: {progress["last_step"]}')
    todos = progress.get('todos') or []
    if todos:
        done = sum(1 for t in todos if t.get('status') == 'completed')
        detail.append(f'plan {done}/{len(todos)} done')
    if detail:
        lines.append('   ' + ' | '.join(detail))
    if task.get('result'):
        result = task['result']
        if result.get('report'):
            lines.append(f'   report: {result["report"]}')
            lines.append(f'   checked: {result.get("quotes_checked")} quotes in {len(result.get("sources_cited") or [])} '
                         'cited sources (the citations match retrieved pages; that is not proof the claims are true)')
        if result.get('summary'):
            lines.append(f'   summary: {result["summary"][:400]}')
    if task.get('error'):
        lines.append(f'   problem: {task["error"]}')
    for event in events or []:
        lines.append(f'   {time.strftime("%H:%M:%S", time.localtime(event["at"]))}  {event["message"]}')
    return '\n'.join(lines)


# ----- running one task --------------------------------------------------------------------------------------------

def _transient(error):
    if getattr(error, 'is_retryable', False) or getattr(error, 'transient', False):
        return True
    try:
        import anthropic
        if isinstance(error, (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError)):
            return True
    except ImportError:
        pass
    return isinstance(error, (ConnectionError, TimeoutError, socket.timeout))


def run_task(db, task, worker_id, saver=None, model=None, search=None, fetch=None, reports_dir=None):
    """Run (or resume) one claimed task to its end, and record the outcome. Returns the final task row."""
    import research
    from langgraph.errors import GraphDrained, GraphRecursionError
    from langgraph.runtime import RunControl
    from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError

    task_id = task['id']
    if task['kind'] != 'research':
        db.finish_task(task_id, 'failed', error=f'Unknown task kind {task["kind"]!r}.', worker_id=worker_id)
        return db.get_task(task_id)
    if task['attempts'] > MAX_ATTEMPTS:
        db.finish_task(task_id, 'failed', error=f'Stopped after {MAX_ATTEMPTS} attempts.', worker_id=worker_id)
        return db.get_task(task_id)

    saver = saver or research.checkpointer()
    agent = research.build_agent(saver, model=model)
    config = research.run_config(task)
    progress = dict(task.get('progress') or {})
    used = float(progress.get('active_seconds', 0.0))
    context = research.TaskContext(task_id=task_id, store=db,
                                   deadline_ts=time.time() + max(0.0, research.MAX_MINUTES * 60 - used),
                                   max_cost_usd=research.MAX_COST_USD,
                                   search=search or research.web.brave_search, fetch=fetch or research.web.fetch_page,
                                   reports_dir=reports_dir)
    control = RunControl()
    finished = threading.Event()
    lost = threading.Event()

    def keep_lease():
        last = time.time()
        while not finished.wait(RENEW_EVERY):
            now = time.time()
            current = (db.get_task(task_id) or {}).get('progress') or {}
            current['active_seconds'] = float(current.get('active_seconds', used)) + (now - last)
            last = now
            db.update_progress(task_id, progress=current)
            if not db.renew_lease(task_id, worker_id, LEASE_SECONDS):
                lost.set()
                control.request_drain('lease lost')
                return
            if db.cancel_requested(task_id):
                control.request_drain('cancelled')
    keeper = threading.Thread(target=keep_lease, name=f'lease-{task_id}', daemon=True)
    keeper.start()

    state = agent.get_state(config)
    resuming = state.created_at is not None
    db.add_event(task_id, 'running', 'Resuming from where it stopped' if resuming else 'Started')
    try:
        if not resuming:
            final = agent.invoke(research.first_message(task), config, context=context, durability='sync',
                                 control=control)
        elif state.next:
            final = agent.invoke(None, config, context=context, durability='sync', control=control)
        else:
            final = state.values           # the run had already ended before the worker stopped
    except GraphDrained:
        finished.set()
        if lost.is_set():
            log.warning('task %s: lease lost; another worker has it', task_id)
            return db.get_task(task_id)
        if db.cancel_requested(task_id):
            db.finish_task(task_id, 'cancelled', error='Cancelled by the owner.', worker_id=worker_id)
        else:
            requeue(db, task_id, worker_id, 'Paused; it will resume.')
        return db.get_task(task_id)
    except research.TaskStopped as stopped:
        finished.set()
        db.finish_task(task_id, 'cancelled' if stopped.kind == 'cancelled' else 'failed', error=stopped.reason,
                       worker_id=worker_id)
        return db.get_task(task_id)
    except ModelCallLimitExceededError:
        finished.set()
        db.finish_task(task_id, 'failed', error=f'Stopped: the task reached its step limit '
                                                f'({research.MAX_MODEL_CALLS} model calls).', worker_id=worker_id)
        return db.get_task(task_id)
    except GraphRecursionError:
        finished.set()
        db.finish_task(task_id, 'failed', error='Stopped: too many steps.', worker_id=worker_id)
        return db.get_task(task_id)
    except Exception as error:  # the run failed; transient ones resume later, others end the task
        finished.set()
        reason = _plain(error)
        if _transient(error) and task['attempts'] < MAX_ATTEMPTS:
            requeue(db, task_id, worker_id, f'{reason} It will be tried again.')
        else:
            log.exception('task %s failed', task_id)
            db.finish_task(task_id, 'failed', error=reason, worker_id=worker_id)
        return db.get_task(task_id)
    finished.set()
    proof = research.evidence(db, task)
    summary = research.summary_of(final)
    if proof:
        db.finish_task(task_id, 'succeeded', result=dict(proof, summary=summary), worker_id=worker_id)
    else:
        db.finish_task(task_id, 'failed', result={'summary': summary} if summary else None,
                       error='The research ended without a saved report whose citations checked out.',
                       worker_id=worker_id)
    return db.get_task(task_id)


def requeue(db, task_id, worker_id, note):
    with db._tx() as conn:
        conn.execute("UPDATE tasks SET status = 'queued', lease_owner = NULL, lease_until = NULL, error = ?, "
                     "updated_at = ? WHERE id = ? AND lease_owner = ?", (note, store_mod.now(), task_id, worker_id))
    db.add_event(task_id, 'queued', note)


def _plain(error):
    """An error as words for the owner, without anything that looks like a key."""
    text = f'{type(error).__name__}: {error}' if str(error) else type(error).__name__
    text = re.sub(r'(sk-ant-[\w-]+|Bearer\s+\S+|X-Subscription-Token\S*|key=\S+)', '[hidden]', text)
    return text[:500]


# ----- the worker and its scheduler --------------------------------------------------------------------------------

def worker(db=None, once=False, idle_exit=IDLE_EXIT, poll=POLL, saver=None, model=None, search=None, fetch=None,
           stop=None, reports_dir=None):
    """Run queued tasks, and queue scheduled ones, until idle (or once). Returns how many tasks it ran."""
    own = db is None
    db = db or store_mod.Store()
    worker_id = f'{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}'
    ran, idle_since = 0, time.time()
    if saver is None:
        import research
        saver = research.checkpointer()
    try:
        while not (stop and stop.is_set()):
            db.beat(worker_id, os.getpid())
            for queued in db.enqueue_due():
                log.info('schedule queued task %s', queued)
            task = db.claim_task(worker_id, LEASE_SECONDS)
            if task is not None:
                try:
                    run_task(db, task, worker_id, saver=saver, model=model, search=search, fetch=fetch,
                             reports_dir=reports_dir)
                except KeyboardInterrupt:
                    requeue(db, task['id'], worker_id, 'The worker was stopped; the task will resume.')
                    raise
                ran += 1
                idle_since = time.time()
                continue
            if once:
                break
            if not any(s['enabled'] for s in db.list_schedules()) and time.time() - idle_since > idle_exit:
                break
            time.sleep(poll)
    except KeyboardInterrupt:
        pass
    finally:
        db.worker_gone(worker_id)
        if own:
            db.close()
    return ran


def ensure_worker(db, python=None):
    """Start a background worker unless one is already running. True when one was started."""
    if db.live_workers():
        return False
    here = os.path.dirname(os.path.abspath(__file__))
    python = python or os.path.join(here, '.venv', 'Scripts', 'pythonw.exe')
    if not os.path.isfile(python):
        python = sys.executable
    flags = 0
    if sys.platform == 'win32':
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen([python, os.path.join(here, 'tasks.py'), 'worker'], cwd=here, creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    return True


EVERY = re.compile(r'^\s*(\d+)\s*([mhdw])\s*$', re.I)


def parse_every(text):
    match = EVERY.match(str(text or ''))
    if not match:
        raise ValueError('Say how often like 30m, 6h, 1d or 1w.')
    seconds = int(match.group(1)) * {'m': 60, 'h': 3600, 'd': 86400, 'w': 604800}[match.group(2).lower()]
    if seconds < 3600:
        raise ValueError('A research schedule repeats at most once an hour.')
    return seconds


# ----- the console -------------------------------------------------------------------------------------------------

def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    import veronica
    veronica.load_env()
    logging.basicConfig(filename=str(store_mod.DATA.parent / 'veronica.log'), level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    for noisy in ('httpx', 'httpx2', 'anthropic', 'langchain_anthropic'):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(description="VERONICA's background tasks")
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('research', help='start a research task')
    p.add_argument('topic')
    p.add_argument('--focus')
    p.add_argument('--wait', action='store_true', help='follow it until it ends')
    p.add_argument('--no-worker', action='store_true', help='do not start a background worker')
    sub.add_parser('list', help='recent tasks')
    p = sub.add_parser('show', help='one task in full')
    p.add_argument('id')
    p = sub.add_parser('cancel', help='cancel a task')
    p.add_argument('id')
    p = sub.add_parser('schedule', help='research a topic again and again')
    p.add_argument('topic')
    p.add_argument('--every', required=True, help='e.g. 1d, 12h, 1w')
    sub.add_parser('schedules', help='list schedules')
    p = sub.add_parser('unschedule', help='delete a schedule')
    p.add_argument('id')
    p = sub.add_parser('worker', help='run tasks in this window')
    p.add_argument('--once', action='store_true', help='stop when the queue is empty')
    p = sub.add_parser('memory', help="what VERONICA remembers")
    p.add_argument('action', choices=['list', 'add', 'edit', 'forget'])
    p.add_argument('rest', nargs='*')
    p.add_argument('--kind', choices=['preference', 'fact'], default='preference')
    args = parser.parse_args(argv)

    if args.command == 'worker':
        ran = worker(once=args.once)
        print(f'The worker stopped after {ran} task(s).')
        return 0
    db = store_mod.Store()
    try:
        if args.command == 'research':
            task = start_research(db, args.topic, focus=args.focus)
            print(f'Queued research task {task["id"]}: {task["title"]}')
            if not args.no_worker and ensure_worker(db):
                print('Started a background worker.')
            if args.wait:
                return follow(db, task['id'])
        elif args.command == 'list':
            tasks = db.list_tasks(limit=20)
            print('\n'.join(describe(t) for t in tasks) if tasks else 'No tasks yet.')
        elif args.command == 'show':
            task = db.get_task(args.id)
            print(describe(task, db.events(args.id)) if task else f'No task {args.id}.')
        elif args.command == 'cancel':
            task = db.request_cancel(args.id)
            print(f'{args.id}: {task["status"]}' + (' (stopping at its next step)' if task['status'] == 'running'
                                                    else '') if task else f'No task {args.id}.')
        elif args.command == 'schedule':
            schedule = db.add_schedule('research', args.topic[:120], {'topic': args.topic}, parse_every(args.every))
            print(f'Schedule {schedule["id"]}: "{args.topic}" every {args.every}, first run now.')
            if ensure_worker(db):
                print('Started a background worker. Scheduled runs happen while a worker is running.')
        elif args.command == 'schedules':
            schedules = db.list_schedules()
            for s in schedules:
                nxt = time.strftime('%Y-%m-%d %H:%M', time.localtime(s['next_run_at']))
                print(f'{s["id"]}  {"on " if s["enabled"] else "off"}  every {s["every_seconds"] // 3600} h  next {nxt}  '
                      f'{s["title"]}')
            if not schedules:
                print('No schedules.')
        elif args.command == 'unschedule':
            print('Deleted.' if db.delete_schedule(args.id) else f'No schedule {args.id}.')
        elif args.command == 'memory':
            return memory_command(db, args)
    finally:
        db.close()
    return 0


def memory_command(db, args):
    stamp = time.strftime('%Y-%m-%d %H:%M')
    if args.action == 'list':
        rows = db.memories()
        for m in rows:
            print(f'{m["id"]:>4}  {m["kind"]:<10}  {m["content"]}\n      ({m["provenance"]}; '
                  f'{time.strftime("%Y-%m-%d", time.localtime(m["updated_at"]))})')
        if not rows:
            print('Nothing remembered yet.')
    elif args.action == 'add':
        text = ' '.join(args.rest).strip()
        if not text:
            print('Say what to remember.')
            return 1
        m = db.remember(args.kind, text[:1000], f'the owner, typed in the console, {stamp}')
        print(f'Remembered ({m["id"]}).')
    elif args.action == 'edit':
        if len(args.rest) < 2 or not args.rest[0].isdigit():
            print('memory edit ID "new text"')
            return 1
        m = db.update_memory(int(args.rest[0]), ' '.join(args.rest[1:])[:1000],
                             f'the owner, corrected in the console, {stamp}')
        print('Updated.' if m else 'No such memory.')
    elif args.action == 'forget':
        if not args.rest or not args.rest[0].isdigit():
            print('memory forget ID')
            return 1
        print('Forgotten.' if db.forget(int(args.rest[0])) else 'No such memory.')
    return 0


def follow(db, task_id, poll=2.0):
    seen = 0
    while True:
        for event in db.events(task_id, after_id=seen):
            print(f'{time.strftime("%H:%M:%S", time.localtime(event["at"]))}  {event["message"]}')
            seen = event['id']
        task = db.get_task(task_id)
        if task['status'] in store_mod.FINISHED:
            print(describe(task))
            return 0 if task['status'] == 'succeeded' else 1
        time.sleep(poll)


if __name__ == '__main__':
    sys.exit(main())
