"""calendar_feed.py (adapted from JARVIS's tests): a Google calendar's iCal feed read for a window of days, with the recurrence rules
Google writes, time zones from the feed's own rules (this PC has no time zone database), the cache, and the plain
refusals. Nothing is fetched from the internet: the opener is a fake."""
import datetime
import os
import unittest
import urllib.error
from unittest.mock import patch

import calendar_feed

FEED = """BEGIN:VCALENDAR
X-WR-CALNAME:Benjamin
BEGIN:VTIMEZONE
TZID:Australia/Sydney
BEGIN:STANDARD
TZOFFSETFROM:+1100
TZOFFSETTO:+1000
TZNAME:AEST
DTSTART:19700405T030000
RRULE:FREQ=YEARLY;BYMONTH=4;BYDAY=1SU
END:STANDARD
BEGIN:DAYLIGHT
TZOFFSETFROM:+1000
TZOFFSETTO:+1100
TZNAME:AEDT
DTSTART:19701004T020000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=1SU
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VEVENT
UID:gym@x
SUMMARY:Gym
DTSTART;TZID=Australia/Sydney:20260921T180000
DTEND;TZID=Australia/Sydney:20260921T190000
RRULE:FREQ=WEEKLY;BYDAY=MO,WE
EXDATE;TZID=Australia/Sydney:20261007T180000
LOCATION:Anytime Fitness\\, Town
END:VEVENT
BEGIN:VEVENT
UID:holiday@x
SUMMARY:Labour Day
DTSTART;VALUE=DATE:20261009
DTEND;VALUE=DATE:20261010
END:VEVENT
BEGIN:VEVENT
UID:call@x
SUMMARY:Call with Jack
DTSTART:20261008T030000Z
DTEND:20261008T033000Z
DESCRIPTION:About the invoice\\nand the next one
END:VEVENT
BEGIN:VEVENT
UID:standup@x
SUMMARY:Standup
DTSTART;TZID=Australia/Sydney:20261006T090000
DTEND;TZID=Australia/Sydney:20261006T091500
RRULE:FREQ=DAILY;COUNT=3
END:VEVENT
BEGIN:VEVENT
UID:standup@x
RECURRENCE-ID;TZID=Australia/Sydney:20261007T090000
SUMMARY:Standup (moved)
DTSTART;TZID=Australia/Sydney:20261007T100000
DTEND;TZID=Australia/Sydney:20261007T101500
END:VEVENT
BEGIN:VEVENT
UID:gone@x
SUMMARY:Cancelled thing
STATUS:CANCELLED
DTSTART:20261008T000000Z
END:VEVENT
BEGIN:VEVENT
UID:board@x
SUMMARY:Board meeting
DTSTART;TZID=Australia/Sydney:20260908T100000
DTEND;TZID=Australia/Sydney:20260908T110000
RRULE:FREQ=MONTHLY;BYDAY=2TU
END:VEVENT
BEGIN:VEVENT
UID:long@x
SUMMARY:Folded title that goes on
  and on
DTSTART;TZID=Australia/Sydney:20261010T200000
DTEND;TZID=Australia/Sydney:20261010T210000
END:VEVENT
END:VCALENDAR
"""
SYDNEY = datetime.timezone(datetime.timedelta(hours=11))
NOW = datetime.datetime(2026, 10, 7, 8, 0, tzinfo=SYDNEY)
URL = 'https://calendar.google.com/calendar/ical/x/private-y/basic.ics'


class FeedTests(unittest.TestCase):
    def setUp(self):
        calendar_feed._cache.clear()

    def test_the_week_ahead_with_recurrence_zones_exceptions_and_overrides(self):
        events, zones = calendar_feed.parse(FEED)
        self.assertEqual([e['summary'] for e in events], ['Gym', 'Labour Day', 'Call with Jack', 'Standup', 'Standup (moved)', 'Board meeting', 'Folded title that goes on and on'])
        self.assertEqual(events[0]['location'], 'Anytime Fitness, Town')
        self.assertEqual(events[2]['description'], 'About the invoice\nand the next one')
        rows = [(e['summary'], s.isoformat() if hasattr(s, 'isoformat') else s) for e, s, _ in calendar_feed.occurrences(events, NOW, NOW + datetime.timedelta(days=7), zones)]
        self.assertEqual(rows, [
            ('Standup (moved)', '2026-10-07T10:00:00+11:00'),      # the override, not the original 09:00
            ('Standup', '2026-10-08T09:00:00+11:00'),              # the third and last of COUNT=3
            ('Call with Jack', '2026-10-08T03:00:00+00:00'),       # a UTC time (14:00 in Sydney)
            ('Labour Day', '2026-10-09'),                          # all day
            ('Folded title that goes on and on', '2026-10-10T20:00:00+11:00'),
            ('Gym', '2026-10-12T18:00:00+11:00'),                  # Wednesday the 7th is an EXDATE; the 14th is past the window
            ('Board meeting', '2026-10-13T10:00:00+11:00'),        # the second Tuesday of October
        ])
        self.assertFalse(zones.fallback_used)   # the feed's own rules gave the zone

    def test_the_zone_rules_from_the_feed_give_daylight_time_in_october_and_standard_time_in_june(self):
        events, zones = calendar_feed.parse(FEED)
        zone = zones.get('Australia/Sydney')
        self.assertEqual(datetime.datetime(2026, 10, 7, 18, tzinfo=zone).utcoffset(), datetime.timedelta(hours=11))
        self.assertEqual(datetime.datetime(2026, 6, 7, 18, tzinfo=zone).utcoffset(), datetime.timedelta(hours=10))
        self.assertEqual(datetime.datetime(2026, 10, 3, 18, tzinfo=zone).utcoffset(), datetime.timedelta(hours=10))   # the day before the change
        self.assertEqual(datetime.datetime(2026, 10, 4, 18, tzinfo=zone).utcoffset(), datetime.timedelta(hours=11))   # the first Sunday
        unknown = zones.get('Mars/Olympus')
        self.assertTrue(zones.fallback_used)
        self.assertIsNotNone(unknown)

    def test_fetch_caches_refuses_and_falls_back_to_the_last_feed(self):
        calls = []

        def opener(url):
            calls.append(url)
            if len(calls) == 3:
                raise urllib.error.HTTPError(url, 403, 'Forbidden', {}, None)
            return FEED
        text, stale = calendar_feed.fetch(URL, opener)
        self.assertEqual((text, stale, len(calls)), (FEED, False, 1))
        self.assertEqual(calendar_feed.fetch(URL, opener), (FEED, False))   # cached: no second fetch
        self.assertEqual(len(calls), 1)
        calendar_feed._cache[URL]['at'] -= calendar_feed.CACHE_SECONDS + 1
        self.assertEqual(calendar_feed.fetch(URL, opener)[1], False)        # fresh again (call 2)
        calendar_feed._cache[URL]['at'] -= calendar_feed.CACHE_SECONDS + 1
        self.assertEqual(calendar_feed.fetch(URL, opener), (FEED, True))    # call 3 refused: the last feed, stale
        calendar_feed._cache.clear()
        with self.assertRaises(calendar_feed.CalendarError) as said:
            calendar_feed.fetch(URL, lambda url: (_ for _ in ()).throw(urllib.error.HTTPError(url, 403, 'Forbidden', {}, None)))
        self.assertIn('refused', str(said.exception))
        self.assertNotIn('private-y', str(said.exception))
        with self.assertRaises(calendar_feed.CalendarError):
            calendar_feed.fetch('http://calendar.google.com/x', opener)
        with self.assertRaises(calendar_feed.CalendarError):
            calendar_feed.fetch(URL, lambda url: '<html>sign in</html>')

    def test_upcoming_rows_for_the_phone_and_the_briefing(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(calendar_feed.configured())
            with self.assertRaises(calendar_feed.CalendarError):
                calendar_feed.upcoming(1, lambda url: FEED, NOW)
        with patch.dict(os.environ, {'GOOGLE_CALENDAR_ICS': URL + ', ' + URL.replace('x', 'z')}, clear=True):
            self.assertEqual(calendar_feed.addresses(), [URL, URL.replace('x', 'z')])
            rows, notes = calendar_feed.upcoming(2, lambda url: FEED, NOW)
        self.assertEqual(notes, [])
        self.assertEqual([r['title'] for r in rows][:3], ['Standup (moved)', 'Standup (moved)', 'Standup'])  # two feeds, the same events twice
        first = rows[0]
        self.assertEqual((first['start'], first['end'], first['all_day'], first['calendar']), ('2026-10-07T10:00+11:00', '2026-10-07T10:15+11:00', False, 'Benjamin'))
        self.assertEqual(first['start_ts'], datetime.datetime(2026, 10, 7, 10, tzinfo=SYDNEY).timestamp())
        holiday = next(r for r in rows if r['title'] == 'Labour Day')
        self.assertEqual((holiday['all_day'], holiday['start'][:10]), (True, '2026-10-09'))
        with patch.dict(os.environ, {'GOOGLE_CALENDAR_ICS': URL}, clear=True):
            calendar_feed._cache.clear()
            result = calendar_feed.test_connection(lambda url: FEED, NOW)
        self.assertEqual((result['status'], result['calendars'], result['events_next_7_days']), ('ok', 1, 7))


if __name__ == '__main__':
    unittest.main()


class ReviewTests(unittest.TestCase):
    """The review's cases: old series, week starts, reminders inside events, durations, odd zone names, one feed failing."""

    def setUp(self):
        calendar_feed._cache.clear()

    def wrap(self, events):
        return 'BEGIN:VCALENDAR\nX-WR-CALNAME:T\n' + events + 'END:VCALENDAR\n'

    def rows(self, feed, days=7, start=NOW):
        events, zones = calendar_feed.parse(feed)
        return [(e['summary'], s.isoformat() if hasattr(s, 'isoformat') else s) for e, s, _ in calendar_feed.occurrences(events, start, start + datetime.timedelta(days=days), zones)], zones

    def test_an_old_daily_series_still_shows_and_count_still_holds(self):
        old = (NOW - datetime.timedelta(days=2100)).strftime('%Y%m%dT070000Z')
        rows, _ = self.rows(self.wrap(f'BEGIN:VEVENT\nUID:p@x\nSUMMARY:Pills\nDTSTART:{old}\nDTEND:{old}\nRRULE:FREQ=DAILY\nEND:VEVENT\n'), days=2)
        self.assertEqual([r[0] for r in rows], ['Pills', 'Pills'])
        counted = (NOW - datetime.timedelta(days=10)).strftime('%Y%m%dT070000Z')
        rows, _ = self.rows(self.wrap(f'BEGIN:VEVENT\nUID:c@x\nSUMMARY:Short\nDTSTART:{counted}\nDTEND:{counted}\nRRULE:FREQ=DAILY;COUNT=5\nEND:VEVENT\n'), days=2)
        self.assertEqual(rows, [])   # the five were over nine days before the window

    def test_week_start_interval_and_the_last_day_of_the_month(self):
        rows, _ = self.rows(self.wrap('BEGIN:VEVENT\nUID:w@x\nSUMMARY:Shift\nDTSTART;VALUE=DATE:20261004\nDTEND;VALUE=DATE:20261005\nRRULE:FREQ=WEEKLY;WKST=SU;INTERVAL=2;BYDAY=SU,MO\nEND:VEVENT\n'),
                             days=20, start=datetime.datetime(2026, 10, 4, 0, 0, tzinfo=SYDNEY))
        self.assertEqual([r[1] for r in rows], ['2026-10-04', '2026-10-05', '2026-10-18', '2026-10-19'])
        rows, _ = self.rows(self.wrap('BEGIN:VEVENT\nUID:m@x\nSUMMARY:Rent\nDTSTART;VALUE=DATE:20260131\nDTEND;VALUE=DATE:20260201\nRRULE:FREQ=MONTHLY;BYMONTHDAY=-1\nEND:VEVENT\n'),
                             days=40, start=datetime.datetime(2026, 10, 1, 0, 0, tzinfo=SYDNEY))
        self.assertEqual([r[1] for r in rows], ['2026-10-31'])
        rows, _ = self.rows(self.wrap('BEGIN:VEVENT\nUID:u@x\nSUMMARY:Course\nDTSTART;VALUE=DATE:20261001\nDTEND;VALUE=DATE:20261002\nRRULE:FREQ=DAILY;UNTIL=20261009\nEND:VEVENT\n'),
                             days=14, start=datetime.datetime(2026, 10, 7, 0, 0, tzinfo=SYDNEY))
        self.assertEqual([r[1] for r in rows], ['2026-10-07', '2026-10-08', '2026-10-09'])   # UNTIL as a date is inclusive

    def test_a_reminder_inside_an_event_a_duration_and_an_odd_zone_name(self):
        feed = self.wrap('BEGIN:VEVENT\nUID:a@x\nSUMMARY:Dentist\nDTSTART:20261008T030000Z\nDURATION:PT45M\n'
                         'BEGIN:VALARM\nACTION:EMAIL\nSUMMARY:Alarm notification\nDESCRIPTION:This is an event reminder\nTRIGGER:-P0DT0H30M0S\nEND:VALARM\nEND:VEVENT\n'
                         'BEGIN:VEVENT\nUID:b@x\nSUMMARY:Outlook one\nDTSTART;TZID="(UTC+10:00) Canberra, Melbourne, Sydney":20261008T090000\nDTEND;TZID="(UTC+10:00) Canberra, Melbourne, Sydney":20261008T100000\nEND:VEVENT\n'
                         'BEGIN:VEVENT\nUID:c@x\nSUMMARY:Broken\nDTSTART:not-a-time\nEND:VEVENT\n')
        events, zones = calendar_feed.parse(feed)
        self.assertEqual([e['summary'] for e in events], ['Dentist', 'Outlook one'])
        self.assertEqual(events[0]['end'] - events[0]['start'], datetime.timedelta(minutes=45))
        self.assertEqual(events[0]['description'], '')
        self.assertEqual(zones.skipped, 1)
        self.assertTrue(zones.fallback_used)   # the quoted zone name is read; its zone falls back to the PC's

    def test_one_failing_calendar_leaves_the_others_readable(self):
        urls = [URL, URL.replace('/x/', '/z/')]

        def opener(url):
            if '/z/' in url:
                raise urllib.error.HTTPError(url, 403, 'Forbidden', {}, None)
            return FEED
        with patch.dict(os.environ, {'GOOGLE_CALENDAR_ICS': ','.join(urls)}, clear=True):
            rows, notes = calendar_feed.upcoming(2, opener, NOW)
            self.assertEqual([r['title'] for r in rows][:2], ['Standup (moved)', 'Standup'])
            self.assertEqual(notes, ['1 calendar could not be fetched: The calendar address was refused (it may have been reset in Google\'s settings)'])
            calendar_feed._cache.clear()
            with self.assertRaises(calendar_feed.CalendarError):
                calendar_feed.upcoming(2, lambda url: (_ for _ in ()).throw(urllib.error.HTTPError(url, 403, 'Forbidden', {}, None)), NOW)
