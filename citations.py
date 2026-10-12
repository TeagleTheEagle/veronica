"""Checking a research report's citations against the sources the task actually retrieved, and saving it once.

What the check proves, and what it does not:
- every [n] in the report is a page this task fetched (it is in the sources table, with its text and hash);
- every cited source has at least one supporting quote, and every quote is found word for word (ignoring case,
  spacing, quote marks and dashes) in that page's text; a quote with "..." must be found piece by piece, in order;
- the report contains no web address that was not retrieved, and the source list is written by VERONICA's program
  from the sources table, not by the model.
It does NOT prove the claims are true, or that a quote supports the sentence it sits next to: it proves that the
citations correspond to real, retrieved text. The saved report says so.
"""
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

import safety

REPORTS = safety.HERE / 'reports'
MIN_SOURCES = 2
MIN_QUOTE_WORDS = 5
CITE = re.compile(r'\[(\d+(?:\s*[,;–-]\s*\d+)*)\]')
URL = re.compile(r'https?://[^\s<>\]\)"\']+')
QUOTE_MARKS = str.maketrans({'“': '"', '”': '"', '‘': "'", '’': "'", '–': '-', '—': '-', ' ': ' '})


def normalize(text):
    text = str(text).translate(QUOTE_MARKS).lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    return ' '.join(text.split())


def cited_numbers(markdown):
    """The source numbers cited in the text: [1], [2, 3], [4-6]."""
    numbers = set()
    for group in CITE.findall(markdown):
        for part in re.split(r'\s*[,;]\s*', group):
            if re.fullmatch(r'\d+\s*[–-]\s*\d+', part):
                low, high = (int(x) for x in re.split(r'\s*[–-]\s*', part))
                if 0 < high - low < 50:
                    numbers.update(range(low, high + 1))
            elif part.strip().isdigit():
                numbers.add(int(part))
    return numbers


def quote_found(quote, source_text):
    """True when the quote (pieces split by '...' in order) is in the source text."""
    pieces = [normalize(p) for p in re.split(r'\.\.\.|…', quote)]
    pieces = [p for p in pieces if p]
    if not pieces:
        return False
    haystack, start = normalize(source_text), 0
    for piece in pieces:
        found = haystack.find(piece, start)
        if found < 0:
            return False
        start = found + len(piece)
    return True


def verify(markdown, citations, sources):
    """{ok, problems, cited, quotes_checked}. `sources`: rows from the store (n, url, text)."""
    by_number = {s['n']: s for s in sources}
    problems = []
    if not markdown or len(markdown.split()) < 80:
        problems.append('The report is too short (fewer than 80 words).')
    cited = cited_numbers(markdown or '')
    if len(cited) < MIN_SOURCES:
        problems.append(f'The report cites {len(cited)} source(s); at least {MIN_SOURCES} different sources are needed.')
    for n in sorted(cited):
        if n not in by_number:
            problems.append(f'[{n}] is cited but no source {n} was retrieved in this task.')
    quotes_by_source = {}
    for item in citations or []:
        if not isinstance(item, dict):
            problems.append('Each citation must be an object with "source" and "quote".')
            continue
        n, quote = item.get('source'), str(item.get('quote') or '').strip()
        if not isinstance(n, int) or n not in by_number:
            problems.append(f'A quote names source {n!r}, which was not retrieved in this task.')
            continue
        if len(normalize(quote).split()) < MIN_QUOTE_WORDS:
            problems.append(f'The quote for [{n}] is shorter than {MIN_QUOTE_WORDS} words: {quote[:80]!r}.')
            continue
        if not quote_found(quote, by_number[n]['text']):
            problems.append(f'The quote for [{n}] was not found in that page\'s text: {quote[:120]!r}. Quote the '
                            'page word for word (use ... between separate parts).')
            continue
        quotes_by_source.setdefault(n, []).append(quote)
    for n in sorted(cited):
        if n in by_number and n not in quotes_by_source:
            problems.append(f'[{n}] is cited but has no supporting quote that was found in the page.')
    retrieved = {s['url'].rstrip('/') for s in sources}
    for url in URL.findall(markdown or ''):
        if url.rstrip('/.,;') not in retrieved and url.rstrip('/') not in retrieved:
            problems.append(f'The report contains an address that was not retrieved: {url[:120]}. Cite with [n] '
                            'instead; the source list is added automatically.')
    return {'ok': not problems, 'problems': problems, 'cited': sorted(cited),
            'quotes_checked': sum(len(q) for q in quotes_by_source.values())}


def render(title, markdown, citations, sources, task_id, checked_at=None):
    """The final report: the model's text, then sources and quotes written from the retrieved records."""
    stamp = time.strftime('%Y-%m-%d %H:%M', time.localtime(checked_at or time.time()))
    cited = cited_numbers(markdown)
    lines = [f'# {title.strip()}', '', markdown.strip(), '', '## Sources', '']
    for s in sources:
        if s['n'] not in cited:
            continue
        fetched = time.strftime('%Y-%m-%d %H:%M', time.localtime(s['fetched_at']))
        lines.append(f'[{s["n"]}] {s["title"].strip() or s["url"]} — {s["url"]} (retrieved {fetched}, '
                     f'text sha256 {s["content_hash"][:12]})')
        for item in citations:
            if isinstance(item, dict) and item.get('source') == s['n']:
                lines.append(f'    > "{str(item.get("quote", "")).strip()}"')
    lines += ['', '---', f'Citation check ({stamp}, task {task_id}): every [n] above points to a page VERONICA '
              'retrieved, and every quote was found word for word in that page. This checks that the citations '
              'correspond to real sources; it does not prove that the claims are true.', '']
    return '\n'.join(lines)


def slug(text):
    words = re.sub(r'[^\w\s-]', '', str(text).lower()).split()
    return '-'.join(words)[:60].strip('-') or 'report'


def save_report(store, task_id, title, content, folder=REPORTS):
    """Save the report once. The operation id is the task's, so a retried or resumed task never writes a second
    report; a save whose outcome is uncertain (the worker stopped mid-way) is reconciled by checking the file on disk
    against the content hash recorded before writing. Returns evidence: path, bytes and the sha256 read back."""
    folder = Path(folder)
    digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
    name = f'{time.strftime("%Y-%m-%d")}-{slug(title)}-{task_id}.md'
    op_id = f'save-report:{task_id}'
    row, created = store.begin_operation(op_id, task_id, 'save_report', str(folder / name),
                                         {'sha256': digest, 'title': title})
    if not created:
        if row['status'] == 'done':
            return dict(row['result'], already_saved=True)
        target = Path(row['target'])
        if target.is_file() and _sha256(target) == row['intent']['sha256']:
            evidence = _evidence(target)
            store.finish_operation(op_id, 'done', evidence)  # it was written before the stop: just record it
            return dict(evidence, reconciled=True)
        if row['intent']['sha256'] != digest:
            # the earlier attempt never landed; record the new intent before writing
            store.finish_operation(op_id, 'pending')
            with store._tx() as db:
                db.execute('UPDATE operations SET intent = ? WHERE op_id = ?',
                           (json.dumps({'sha256': digest, 'title': title}), op_id))
        target_path = target
    else:
        target_path = folder / name
    target_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=str(target_path.parent), prefix='.saving-', suffix='.md')
    try:
        with os.fdopen(handle, 'wb') as out:
            out.write(content.encode('utf-8'))
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, target_path)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        store.finish_operation(op_id, 'uncertain')
        raise
    evidence = _evidence(target_path)
    if evidence['sha256'] != digest:
        store.finish_operation(op_id, 'failed', evidence)
        raise OSError('The saved report does not match what was written.')
    store.finish_operation(op_id, 'done', evidence)
    return evidence


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _evidence(path):
    path = Path(path)
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': _sha256(path)}
