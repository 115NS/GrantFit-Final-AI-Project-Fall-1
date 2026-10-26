"""Safe retrieval of public funder pages (untrusted input).

- http(s) only; hosts resolving to private, loopback, or link-local addresses are refused
- short timeouts, size cap, HTML/plain text only (PDFs are reported, not parsed)
- HTML is converted to plain text with scripts, styles, and hidden elements dropped
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

import requests

USER_AGENT = "GrantFit/0.4 (nonprofit funding research tool; educational project)"
MAX_BYTES = 2_000_000
MAX_TEXT_CHARS = 20_000
TIMEOUT = (5, 12)  # connect, read seconds


class FetchError(Exception):
    pass


@dataclass
class FetchedPage:
    url: str                   # final URL after redirects
    text: str
    last_modified: str | None
    truncated: bool
    title: str = ""


def canonical_url(url: str) -> str:
    """Normalize for de-duplication: lowercase host, no www., no fragment/tracking, no trailing slash."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    query = "&".join(q for q in parts.query.split("&")
                     if q and not q.lower().startswith(("utm_", "gclid", "fbclid", "ref=")))
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, query, ""))


def check_public_http_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("Only public http(s) web addresses can be retrieved.")
    try:
        infos = socket.getaddrinfo(parts.hostname, None)
    except socket.gaierror:
        raise FetchError("The web address could not be resolved.") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise FetchError("Private or local network addresses are not retrieved.")


class _TextExtractor(HTMLParser):
    """Visible-text extraction. Non-content elements are skipped; everything kept is still treated
    as untrusted and passes the injection scan downstream."""

    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "head"}
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table",
             "section", "article", "header", "footer", "nav", "main", "aside", "dd", "dt", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip_depth += 1
        elif tag in self.BLOCK:
            self.parts.append("\n- " if tag == "li" else "\n")

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    """Return (title, visible text)."""
    p = _TextExtractor()
    p.feed(html)
    text = "".join(p.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return re.sub(r"\s+", " ", p.title).strip(), text


def fetch_page(url: str, session: requests.Session | None = None) -> FetchedPage:
    check_public_http_url(url)
    s = session or requests.Session()
    try:
        resp = s.get(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9"},
                     timeout=TIMEOUT, stream=True, allow_redirects=True)
    except requests.Timeout:
        raise FetchError("The page took too long to respond.") from None
    except requests.RequestException as exc:
        raise FetchError(f"The page could not be retrieved ({type(exc).__name__}).") from None
    check_public_http_url(resp.url)   # redirects must also stay public
    if resp.status_code >= 400:
        raise FetchError(f"The site returned HTTP {resp.status_code}.")
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "pdf" in ctype:
        raise FetchError("The link is a PDF; GrantFit does not parse PDFs. Open it and paste the text.")
    if "html" not in ctype and "text/plain" not in ctype:
        raise FetchError(f"Unsupported content type ({ctype or 'unknown'}).")
    body = b""
    for chunk in resp.iter_content(65536):
        body += chunk
        if len(body) > MAX_BYTES:
            break
    raw = body.decode(resp.encoding or "utf-8", errors="replace")
    title, text = html_to_text(raw) if "html" in ctype else ("", raw)
    truncated = len(text) > MAX_TEXT_CHARS
    return FetchedPage(url=resp.url, text=text[:MAX_TEXT_CHARS], truncated=truncated,
                       last_modified=resp.headers.get("Last-Modified"), title=title)
