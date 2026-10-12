"""Google Calendar for VERONICA from a calendar's secret iCal address. VERONICA's .env holds GOOGLE_CALENDAR_ICS (one
address, or several separated by commas); the feed is fetched at most every ten minutes. Nothing is written to any
calendar: an iCal feed can only be read. The address is a secret: it is never shown, logged or given to the model.

Adapted from JARVIS's calendar_feed.py (the owner's own code), as VERONICA's separate copy.

The feed is iCalendar text (RFC 5545) as Google writes it: VEVENTs with DTSTART/DTEND (a date, a UTC time or a time in
a named zone), recurring events as one VEVENT with an RRULE plus EXDATEs and overridden instances carrying a
RECURRENCE-ID. Enough of the recurrence rules is expanded here for a window of days: DAILY, WEEKLY (BYDAY), MONTHLY
(BYMONTHDAY or one BYDAY such as 2TU), YEARLY, with INTERVAL, COUNT and UNTIL. Zones come from zoneinfo when the time
zone database is there, else from the feed's own VTIMEZONE rules, else the PC's local zone (said in the answer).
"""
import datetime
import http.client
import os
import re
import time
import urllib.error
import urllib.request

import safety

CACHE_SECONDS = 600
MAX_BYTES = 5 * 1024 * 1024
MAX_OCCURRENCES = 2000
NOT_CONFIGURED = "The calendar is not set up: put GOOGLE_CALENDAR_ICS in VERONICA's .env and start VERONICA again."
WEEKDAYS = {'MO': 0, 'TU': 1, 'WE': 2, 'TH': 3, 'FR': 4, 'SA': 5, 'SU': 6}
_cache = {}   # url -> {'at', 'text'}


class CalendarError(ValueError):
    """Said as it is; never the address itself (it is a secret)."""


def configured():
    return bool(addresses())


def addresses():
    return [a.strip() for a in os.getenv('GOOGLE_CALENDAR_ICS', '').split(',') if a.strip()]


def fetch(url, opener=None):
    """The feed's text, from the cache when fresh. `opener` is replaced in tests."""
    if not re.fullmatch(r'https://[A-Za-z0-9.\-]+/[^\s]+', url):
        raise CalendarError('A calendar address starts with https:// (use the secret address in iCal format).')
    cached = _cache.get(url)
    now = time.time()
    if cached and now - cached['at'] < CACHE_SECONDS:
        return cached['text'], False
    try:
        if opener:
            text = opener(url)
        else:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'VERONICA'}), timeout=25) as response:
                text = response.read(MAX_BYTES + 1).decode('utf-8', 'replace')
        if len(text) > MAX_BYTES:
            raise CalendarError('The calendar feed is too large to read.')
        if 'BEGIN:VCALENDAR' not in text:
            raise CalendarError('The calendar address did not answer with a calendar (use the secret address in iCal format).')
    except CalendarError:
        raise
    except urllib.error.HTTPError as error:
        if cached:
            return cached['text'], True
        raise CalendarError('The calendar address was refused (it may have been reset in Google\'s settings).' if error.code in (401, 403, 404)
                            else 'The calendar could not be fetched.') from None
    except (OSError, ValueError, http.client.HTTPException):
        if cached:
            return cached['text'], True
        raise CalendarError('The calendar could not be fetched. Check the connection and try again.') from None
    _cache[url] = {'at': now, 'text': text}
    return text, False


# ---------------------------------------------------------------- parsing
def unfold(text):
    lines = []
    for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if line[:1] in (' ', '\t') and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _property(line):
    """NAME;PARAM=VALUE;...:value -> (name, {param: value}, value). A quoted parameter may hold a colon
    (TZID="(UTC+10:00) Canberra"), so the separating colon is the first one outside quotes."""
    quoted, split = False, len(line)
    for i, c in enumerate(line):
        if c == '"':
            quoted = not quoted
        elif c == ':' and not quoted:
            split = i
            break
    head, value = line[:split], line[split + 1:]
    parts = head.split(';')
    params = {}
    for part in parts[1:]:
        key, _, val = part.partition('=')
        params[key.upper()] = val.strip('"')
    return parts[0].upper(), params, value


def _unescape(value):
    return value.replace('\\n', '\n').replace('\\N', '\n').replace('\\,', ',').replace('\\;', ';').replace('\\\\', '\\')


class Zones:
    """A zone by its TZID: zoneinfo when the database is there, else the feed's VTIMEZONE rules, else the local zone."""

    def __init__(self, timezones):
        self.rules, self.fallback_used, self.skipped = timezones, False, 0

    def get(self, tzid):
        if not tzid:
            return None
        try:
            import zoneinfo
            return zoneinfo.ZoneInfo(tzid)
        except Exception:
            pass
        rules = self.rules.get(tzid)
        if rules:
            return _RuleZone(tzid, rules)
        self.fallback_used = True
        return datetime.datetime.now().astimezone().tzinfo


def _offset(text):
    match = re.fullmatch(r'([+-])(\d{2})(\d{2})(\d{2})?', text or '')
    if not match:
        return datetime.timedelta(0)
    sign = 1 if match.group(1) == '+' else -1
    return sign * datetime.timedelta(hours=int(match.group(2)), minutes=int(match.group(3)), seconds=int(match.group(4) or 0))


class _RuleZone(datetime.tzinfo):
    """A zone from a VTIMEZONE with one STANDARD and at most one DAYLIGHT component whose transitions are a yearly
    BYMONTH/BYDAY rule (as Google writes them)."""

    def __init__(self, name, components):
        self.name, self.components = name, components

    def _transition(self, component, year):
        start = component.get('DTSTART')
        rule = component.get('RRULE') or {}
        if not start:
            return None
        month = int(rule.get('BYMONTH', start.month))
        byday = rule.get('BYDAY')
        if byday:
            match = re.fullmatch(r'([+-]?\d)?([A-Z]{2})', byday)
            n, weekday = int(match.group(1) or 1), WEEKDAYS[match.group(2)]
            day = _nth_weekday(year, month, weekday, n)
        else:
            day = datetime.date(year, month, start.day)
        return datetime.datetime.combine(day, start.time())  # wall time before the change

    def _current(self, dt):
        standard = self.components.get('STANDARD')
        daylight = self.components.get('DAYLIGHT')
        if not daylight or not standard:
            return standard or daylight or {}
        naive = dt.replace(tzinfo=None)
        to_daylight, to_standard = self._transition(daylight, naive.year), self._transition(standard, naive.year)
        if to_daylight is None or to_standard is None:
            return standard
        if to_daylight < to_standard:   # northern: daylight between the two
            return daylight if to_daylight <= naive < to_standard else standard
        return standard if to_standard <= naive < to_daylight else daylight   # southern: standard between the two

    def utcoffset(self, dt):
        return _offset(self._current(dt).get('TZOFFSETTO'))

    def dst(self, dt):
        current = self._current(dt)
        return _offset(current.get('TZOFFSETTO')) - _offset(current.get('TZOFFSETFROM')) if current is self.components.get('DAYLIGHT') else datetime.timedelta(0)

    def tzname(self, dt):
        return self._current(dt).get('TZNAME') or self.name


def _nth_weekday(year, month, weekday, n):
    if n > 0:
        first = datetime.date(year, month, 1)
        day = first + datetime.timedelta(days=(weekday - first.weekday()) % 7)
        return day + datetime.timedelta(weeks=n - 1)
    last = (datetime.date(year + (month == 12), month % 12 + 1, 1) - datetime.timedelta(days=1))
    day = last - datetime.timedelta(days=(last.weekday() - weekday) % 7)
    return day + datetime.timedelta(weeks=n + 1)


def _when(value, params, zones):
    """A DTSTART/DTEND/EXDATE/RECURRENCE-ID value: a date (all day) or an aware datetime."""
    value = value.strip()
    if params.get('VALUE') == 'DATE' or re.fullmatch(r'\d{8}', value):
        return datetime.date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    match = re.fullmatch(r'(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)', value)
    if not match:
        raise CalendarError('The calendar feed has a time VERONICA cannot read.')
    naive = datetime.datetime(*(int(match.group(i)) for i in range(1, 7)))
    if match.group(7) == 'Z':
        return naive.replace(tzinfo=datetime.timezone.utc)
    zone = zones.get(params.get('TZID')) or datetime.datetime.now().astimezone().tzinfo
    return naive.replace(tzinfo=zone)


def _rrule(value):
    rule = {}
    for part in value.split(';'):
        key, _, val = part.partition('=')
        rule[key.upper()] = val
    return rule


def parse(text):
    """The feed's events and time zones: (events, Zones). An event: uid, summary, start, end, all_day, location,
    description, rrule, exdates, recurrence_id, status, calendar (the feed's name)."""
    lines = unfold(text)
    name, events, timezones = '', [], {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('X-WR-CALNAME:'):
            name = _unescape(line.split(':', 1)[1]).strip()
        elif line == 'BEGIN:VTIMEZONE':
            tzid, components, current, block = None, {}, None, {}
            i += 1
            while i < len(lines) and lines[i] != 'END:VTIMEZONE':
                prop, params, value = _property(lines[i])
                if prop == 'TZID':
                    tzid = value
                elif prop == 'BEGIN' and value in ('STANDARD', 'DAYLIGHT'):
                    current, block = value, {}
                elif prop == 'END' and current:
                    components[current] = block
                    current = None
                elif current:
                    if prop == 'DTSTART':
                        match = re.fullmatch(r'(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})', value.strip())
                        block['DTSTART'] = datetime.datetime(*(int(match.group(k)) for k in range(1, 7))) if match else None
                    elif prop == 'RRULE':
                        block['RRULE'] = _rrule(value)
                    else:
                        block[prop] = value
                i += 1
            if tzid:
                timezones[tzid] = components
        elif line == 'BEGIN:VEVENT':
            raw, alarm = [], False
            i += 1
            while i < len(lines) and lines[i] != 'END:VEVENT':
                # A reminder (VALARM) inside the event has its own SUMMARY and DESCRIPTION: not the event's.
                if lines[i] == 'BEGIN:VALARM':
                    alarm = True
                elif lines[i] == 'END:VALARM':
                    alarm = False
                elif not alarm:
                    raw.append(lines[i])
                i += 1
            events.append(raw)
        i += 1
    zones = Zones(timezones)
    parsed = []
    for raw in events:
        try:
            event = _event(raw, zones, name)
        except CalendarError:
            zones.skipped += 1  # one unreadable event never costs the whole calendar; said in a note
            continue
        if event is not None:
            parsed.append(event)
    return parsed, zones


def _duration(value):
    """An iCalendar DURATION (P1DT2H30M, PT45M, P2W) as a timedelta; None when unreadable."""
    match = re.fullmatch(r'([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?', value.strip())
    if not match:
        return None
    sign = -1 if match.group(1) == '-' else 1
    weeks, days, hours, minutes, seconds = (int(g or 0) for g in match.groups()[1:])
    return sign * datetime.timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)


def _event(raw, zones, name):
    """One VEVENT's lines as an event, or None for one that is cancelled or has no start."""
    if True:
        event = {'uid': '', 'summary': '(untitled)', 'start': None, 'end': None, 'all_day': False, 'location': '', 'description': '',
                 'rrule': None, 'exdates': [], 'recurrence_id': None, 'status': 'CONFIRMED', 'calendar': name}
        duration = None
        for line in raw:
            prop, params, value = _property(line)
            if prop == 'UID':
                event['uid'] = value
            elif prop == 'SUMMARY':
                event['summary'] = _unescape(value).strip() or '(untitled)'
            elif prop == 'LOCATION':
                event['location'] = _unescape(value).strip()
            elif prop == 'DESCRIPTION':
                event['description'] = _unescape(value).strip()[:500]
            elif prop == 'STATUS':
                event['status'] = value.upper()
            elif prop == 'DTSTART':
                event['start'] = _when(value, params, zones)
                event['all_day'] = isinstance(event['start'], datetime.date) and not isinstance(event['start'], datetime.datetime)
            elif prop == 'DTEND':
                event['end'] = _when(value, params, zones)
            elif prop == 'RRULE':
                event['rrule'] = _rrule(value)
            elif prop == 'EXDATE':
                event['exdates'].extend(_when(v, params, zones) for v in value.split(','))
            elif prop == 'RECURRENCE-ID':
                event['recurrence_id'] = _when(value, params, zones)
            elif prop == 'DURATION':
                duration = _duration(value)
        if event['start'] is None or event['status'] == 'CANCELLED':
            return None
        if event['end'] is None:
            if duration is not None and not event['all_day']:
                event['end'] = event['start'] + duration
            elif duration is not None and event['all_day'] and duration.days >= 1:
                event['end'] = event['start'] + datetime.timedelta(days=duration.days)
            else:
                event['end'] = event['start'] + datetime.timedelta(days=1) if event['all_day'] else event['start']
        return event


# ---------------------------------------------------------------- recurrence
def _step(start, rule, n):
    """The n-th candidate start of a rule (n = 0 is the first)."""
    freq, interval = rule.get('FREQ', 'DAILY'), max(1, int(rule.get('INTERVAL', 1) or 1))
    if freq == 'DAILY':
        return start + datetime.timedelta(days=n * interval)
    if freq == 'WEEKLY':
        return start + datetime.timedelta(weeks=n * interval)
    if freq == 'MONTHLY':
        month = start.month - 1 + n * interval
        year, month = start.year + month // 12, month % 12 + 1
        byday = rule.get('BYDAY')
        if byday:
            match = re.fullmatch(r'([+-]?\d)([A-Z]{2})', byday)
            if match:
                day = _nth_weekday(year, month, WEEKDAYS[match.group(2)], int(match.group(1)))
                return _same_time(start, day)
        day = int(str(rule.get('BYMONTHDAY', start.day)).split(',')[0])
        if day < 0:  # -1 is the last day of the month
            last = (datetime.date(year + (month == 12), month % 12 + 1, 1) - datetime.timedelta(days=1)).day
            day = last + 1 + day
        try:
            return _same_time(start, datetime.date(year, month, day))
        except ValueError:
            return None
    if freq == 'YEARLY':
        try:
            return _same_time(start, datetime.date(start.year + n * interval, start.month, start.day))
        except ValueError:
            return None
    return None


def _same_time(start, day):
    if isinstance(start, datetime.datetime):
        return datetime.datetime.combine(day, start.time(), tzinfo=start.tzinfo)
    return day


def _weekly_days(start, rule):
    byday = rule.get('BYDAY')
    if not byday:
        return [start.weekday()]
    return sorted(WEEKDAYS[d] for d in byday.split(',') if d in WEEKDAYS)


def _as_datetime(value, zone):
    """A date or datetime as an aware datetime, for comparing."""
    if isinstance(value, datetime.datetime):
        return value
    return datetime.datetime.combine(value, datetime.time(), tzinfo=zone)


def occurrences(events, window_start, window_end, zones=None):
    """Every occurrence of every event that overlaps [window_start, window_end): (event, start, end), sorted."""
    local = window_start.tzinfo
    overrides = {(e['uid'], _as_datetime(e['recurrence_id'], local).astimezone(datetime.timezone.utc)) for e in events if e['recurrence_id']}
    found = []
    for event in events:
        length = event['end'] - event['start']
        if event['recurrence_id'] or not event['rrule']:
            found.append((event, event['start'], event['end']))
            continue
        rule = event['rrule']
        until = rule.get('UNTIL')
        until_at = _when(until, {}, zones or Zones({})) if until else None
        count = int(rule['COUNT']) if rule.get('COUNT') else None
        exdates = {_as_datetime(x, local).astimezone(datetime.timezone.utc) for x in event['exdates']}
        freq, interval = rule.get('FREQ', 'DAILY'), max(1, int(rule.get('INTERVAL', 1) or 1))
        # An old daily or weekly series is not walked from its first day: the first step near the window is computed
        # (one step early), and the steps before it count toward COUNT as if walked.
        first = _as_datetime(event['start'], local)
        unit = datetime.timedelta(days=interval) if freq == 'DAILY' else datetime.timedelta(weeks=interval) if freq == 'WEEKLY' else None
        n0 = max(0, int((window_start - first) / unit) - 1) if unit and window_start > first else 0
        made, n = n0, n0
        candidates = []
        if freq == 'WEEKLY' and rule.get('BYDAY'):
            wkst = WEEKDAYS.get(rule.get('WKST', 'MO'), 0)
            days = sorted(_weekly_days(event['start'], rule), key=lambda d: (d - wkst) % 7)
            week0 = event['start'] - datetime.timedelta(days=(event['start'].weekday() - wkst) % 7)
            if n0:  # the skipped weeks' occurrences, for COUNT: week 0 from the start day on, then whole weeks
                made = len([d for d in days if week0 + datetime.timedelta(days=(d - wkst) % 7) >= event['start']]) + (n0 - 1) * len(days)
            while n < n0 + MAX_OCCURRENCES:
                week = _step(week0, rule, n)
                if week is None:
                    break
                stop = False
                for weekday in days:
                    candidate = week + datetime.timedelta(days=(weekday - wkst) % 7)
                    if candidate < event['start']:
                        continue
                    candidates.append(candidate)
                    if _as_datetime(candidate, local) >= window_end:
                        stop = True
                        break
                if stop:
                    break
                n += 1
        else:
            while n < n0 + MAX_OCCURRENCES:
                candidate = _step(event['start'], rule, n)
                n += 1
                if candidate is None:
                    continue
                candidates.append(candidate)
                if _as_datetime(candidate, local) >= window_end:
                    break
        for candidate in candidates:
            if until_at is not None and _as_datetime(candidate, local) > _as_datetime(until_at, local):
                break
            made += 1
            if count is not None and made > count:
                break
            key = _as_datetime(candidate, local).astimezone(datetime.timezone.utc)
            if key in exdates or (event['uid'], key) in overrides:
                continue
            found.append((event, candidate, candidate + length))
    chosen = []
    for event, start, end in found:
        s, e = _as_datetime(start, local), _as_datetime(end, local)
        if s < window_end and e > window_start:
            chosen.append((event, start, end))
    chosen.sort(key=lambda item: _as_datetime(item[1], local))
    return chosen


# ---------------------------------------------------------------- what VERONICA reads
def events_between(start, end, opener=None, now=None):
    """The events of every configured calendar in [start, end), as plain rows: title, start, end (local ISO text and
    epoch seconds), all_day, location, calendar; plus a note when a feed was stale or a zone fell back."""
    if not configured():
        raise CalendarError(NOT_CONFIGURED)
    local = start.tzinfo
    rows, notes, failed = [], [], []
    for url in addresses():
        try:
            text, stale = fetch(url, opener)
        except CalendarError as why:
            failed.append(why)  # one calendar's trouble is said in a note; the others are still read
            continue
        events, zones = parse(text)
        if zones.skipped:
            notes.append(f"{zones.skipped} event{'s' if zones.skipped != 1 else ''} in a calendar could not be read and {'were' if zones.skipped != 1 else 'was'} left out")
        for event, when, until in occurrences(events, start, end, zones):
            # Each event in this PC's local time for its own date (a daylight-saving change in the window moves it).
            s, e = _as_datetime(when, local).astimezone(), _as_datetime(until, local).astimezone()
            rows.append({'title': safety.one_line(event['summary']), 'start': s.isoformat(timespec='minutes'),
                         'end': e.isoformat(timespec='minutes'), 'start_ts': s.timestamp(), 'end_ts': e.timestamp(),
                         'all_day': event['all_day'], 'location': safety.one_line(event['location'], 200) or None,
                         'calendar': safety.one_line(event['calendar'], 100) or None})
        if stale:
            notes.append('the calendar could not be fetched just now; these are the events as last read')
        if zones.fallback_used:
            notes.append('a time zone in the feed was not recognised; its times are shown as this PC\'s local time')
    if failed and len(failed) == len(addresses()):
        raise failed[-1]
    if failed:
        notes.append(f"{len(failed)} calendar{'s' if len(failed) != 1 else ''} could not be fetched: " + str(failed[-1]).rstrip('.'))
    rows.sort(key=lambda r: r['start_ts'])
    return rows, sorted(set(notes))


def upcoming(days=1, opener=None, now=None):
    """The next `days` days from now."""
    now = now or datetime.datetime.now().astimezone()
    return events_between(now, now + datetime.timedelta(days=max(1, min(int(days), 60))), opener, now)


def test_connection(opener=None, now=None):
    """Each feed fetched once; how many events in the next seven days."""
    if not configured():
        raise CalendarError(NOT_CONFIGURED)
    _cache.clear()
    rows, notes = upcoming(7, opener, now)
    return {'status': 'ok', 'calendars': len(addresses()), 'events_next_7_days': len(rows), 'notes': notes}
