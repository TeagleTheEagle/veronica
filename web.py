"""The web, for research: Brave Search and fetching the pages it finds.

Everything here treats the web as untrusted data:
- only http and https; never this PC, the home network or other private addresses (checked for the first address and
  for every redirect), so a page cannot steer VERONICA at the router or at JARVIS;
- size and time limits on every request; HTML is turned into plain text (scripts and styles dropped);
- transient failures (timeouts, 429, 5xx) are retried a bounded number of times with backoff, honouring Retry-After;
  anything else fails at once with a plain error. The Brave key travels in a header only and is never logged.
"""
import gzip
import hashlib
import html.parser
import ipaddress
import json
import os
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

USER_AGENT = 'VERONICA/1.0 (personal research assistant)'
TIMEOUT = 20
MAX_BYTES = 3_000_000
MAX_TEXT = 200_000
RETRIES = 3
MAX_RETRY_AFTER = 30
TEXT_TYPES = ('text/html', 'application/xhtml+xml', 'text/plain', 'text/markdown')
TRANSIENT_CODES = {408, 425, 429, 500, 502, 503, 504}


class WebError(RuntimeError):
    """A request that failed. transient: worth retrying later."""

    def __init__(self, message, transient=False, status=None):
        super().__init__(message)
        self.transient, self.status = transient, status


# ----- where requests may go ---------------------------------------------------------------------------------------

def check_url(url):
    """The URL if it is a public http(s) address, else WebError."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise WebError(f'Only http and https web addresses can be fetched, not {url[:80]!r}.')
    host = parts.hostname
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == 'https' else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise WebError(f'The address {host} could not be found.', transient=True) from None
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split('%')[0])
        if (address.is_private or address.is_loopback or address.is_link_local or address.is_multicast
                or address.is_reserved or address.is_unspecified):
            raise WebError(f'{host} is on this PC or a private network; VERONICA only fetches public web pages.')
    return url


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_CheckedRedirects)


def _retry_after(headers):
    value = (headers or {}).get('Retry-After') if headers else None
    try:
        return min(MAX_RETRY_AFTER, max(0.0, float(value)))
    except (TypeError, ValueError):
        return None


def request(url, headers=None, timeout=TIMEOUT, max_bytes=MAX_BYTES, retries=RETRIES, sleep=time.sleep,
            opener=None):
    """(final URL, content type, body bytes) of a GET, with the checks and retries above."""
    check_url(url)
    attempt = 0
    while True:
        attempt += 1
        try:
            return _get_once(url, headers or {}, timeout, max_bytes, opener or _opener)
        except WebError as problem:
            if not problem.transient or attempt > retries:
                raise
            wait = getattr(problem, 'retry_after', None)
            if wait is None:
                wait = min(8.0, 2 ** (attempt - 1)) + random.uniform(0, 0.5)
            sleep(wait)


def _get_once(url, headers, timeout, max_bytes, opener):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept-Encoding': 'gzip, deflate',
                                               **headers})
    try:
        with opener.open(req, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise WebError(f'The page is larger than {max_bytes // 1_000_000} MB; not fetched.')
            encoding = (response.headers.get('Content-Encoding') or '').lower()
            if encoding == 'gzip':
                body = gzip.decompress(body)
            elif encoding == 'deflate':
                body = zlib.decompress(body)
            return response.geturl(), response.headers.get('Content-Type', ''), body
    except urllib.error.HTTPError as error:
        problem = WebError(f'The server answered {error.code}.', transient=error.code in TRANSIENT_CODES,
                           status=error.code)
        problem.retry_after = _retry_after(error.headers)
        raise problem from None
    except WebError:
        raise
    except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as error:
        reason = getattr(error, 'reason', error)
        if isinstance(reason, WebError):
            raise reason from None
        raise WebError(f'The request did not complete ({reason}).', transient=True) from None


# ----- Brave Search ------------------------------------------------------------------------------------------------

BRAVE_URL = 'https://api.search.brave.com/res/v1/web/search'


def brave_search(query, count=8, key=None, fetch=request):
    """A list of {title, url, description} from Brave's web search."""
    key = key or os.environ.get('BRAVE_API_KEY', '').strip()
    if not key:
        raise WebError('Web search needs BRAVE_API_KEY in VERONICA\'s .env.')
    query = ' '.join(str(query).split())[:400]
    if not query:
        raise WebError('An empty search was asked for.')
    url = BRAVE_URL + '?' + urllib.parse.urlencode({'q': query, 'count': max(1, min(20, int(count)))})
    try:
        _, _, body = fetch(url, headers={'Accept': 'application/json', 'X-Subscription-Token': key})
    except WebError as problem:
        if problem.status in (401, 403):
            raise WebError('Brave refused the key (check BRAVE_API_KEY).', status=problem.status) from None
        if problem.status == 429:
            raise WebError('Brave is limiting searches right now (429).', transient=True, status=429) from None
        raise
    try:
        data = json.loads(body.decode('utf-8'))
    except ValueError:
        raise WebError('Brave sent an answer that could not be read.', transient=True) from None
    results = []
    for item in ((data.get('web') or {}).get('results') or []):
        if isinstance(item, dict) and item.get('url'):
            results.append({'title': _clean(item.get('title', '')), 'url': item['url'],
                            'description': _clean(item.get('description', ''))})
    return results


# ----- pages -------------------------------------------------------------------------------------------------------

class _Text(html.parser.HTMLParser):
    SKIP = {'script', 'style', 'noscript', 'svg', 'template', 'iframe', 'head'}
    BLOCK = {'p', 'div', 'br', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'article', 'blockquote',
             'pre', 'table', 'ul', 'ol', 'header', 'footer', 'nav', 'main', 'dd', 'dt', 'figcaption'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title, self.skip, self.in_title = [], '', 0, False

    def handle_starttag(self, tag, attrs):
        if tag == 'title':
            self.in_title = True
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append('\n')

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if not self.skip:
            self.parts.append(data)


def _clean(text):
    parser = _Text()
    parser.feed(str(text))
    return ' '.join(''.join(parser.parts).split())


def html_to_text(markup):
    """(title, text) of an HTML page: visible text only, one line per block."""
    parser = _Text()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # html.parser is forgiving, but never let a strange page stop the task
        pass
    lines = (' '.join(line.split()) for line in ''.join(parser.parts).splitlines())
    return ' '.join(parser.title.split()), '\n'.join(line for line in lines if line)


def fetch_page(url, fetch=request):
    """{url, title, text, content_hash} of a public web page, as plain text."""
    final_url, content_type, body = fetch(url)
    kind = content_type.split(';')[0].strip().lower()
    if kind and kind not in TEXT_TYPES:
        raise WebError(f'That address is a {kind} file, not a web page; only pages and plain text are read.')
    charset = 'utf-8'
    for part in content_type.split(';')[1:]:
        if part.strip().lower().startswith('charset='):
            charset = part.split('=', 1)[1].strip().strip('"') or 'utf-8'
    try:
        markup = body.decode(charset, errors='replace')
    except LookupError:
        markup = body.decode('utf-8', errors='replace')
    if kind in ('text/plain', 'text/markdown'):
        title, text = '', '\n'.join(line.rstrip() for line in markup.splitlines())
    else:
        title, text = html_to_text(markup)
    text = text[:MAX_TEXT]
    if len(text.strip()) < 40:
        raise WebError('That page has almost no readable text (it may need JavaScript).')
    return {'url': final_url, 'title': title or final_url, 'text': text,
            'content_hash': hashlib.sha256(text.encode('utf-8')).hexdigest()}
