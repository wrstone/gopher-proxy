#!/usr/bin/env python3
"""HTML-to-Gopher proxy: browse Gopher sites in a web browser."""

from __future__ import annotations

import html
import os
import re
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NamedTuple
from urllib.parse import parse_qs, quote, unquote, urlparse

DEFAULT_PORT = 8765
BIND_HOST = os.environ.get("BIND_HOST", "0.0.0.0")
DEFAULT_GOPHER = os.environ.get("GOPHER_START", "gopher://sdf.org/users/wrstone/")
GO_TIMEOUT = 20
MAX_RESPONSE = 5 * 1024 * 1024

# Gopher item types that return a menu listing.
MENU_TYPES = frozenset("124567")

# Types rendered as plain text.
TEXT_TYPES = frozenset("0")

# Binary types served inline or as download.
BINARY_TYPES = frozenset("9gGI")

TYPE_LABELS = {
    "0": "Text",
    "1": "Directory",
    "2": "CSO phone book",
    "3": "Error",
    "4": "BinHex",
    "5": "DOS archive",
    "6": "UUEncoded",
    "7": "Search",
    "8": "Telnet",
    "9": "Binary",
    "g": "GIF",
    "h": "HTML",
    "i": "Info",
    "I": "Image",
    "s": "Audio",
    "T": "TN3270",
}


class GopherItem(NamedTuple):
    type: str
    name: str
    selector: str
    host: str
    port: int


class GopherError(Exception):
    pass


def fetch_gopher(host: str, port: int, selector: str, search: str | None = None) -> bytes:
    try:
        with socket.create_connection((host, port), timeout=GO_TIMEOUT) as sock:
            sock.settimeout(GO_TIMEOUT)
            if search is not None:
                payload = f"{selector}\t{search}\r\n"
            else:
                payload = f"{selector}\r\n"
            sock.sendall(payload.encode("utf-8", errors="replace"))
            chunks: list[bytes] = []
            total = 0
            while True:
                try:
                    block = sock.recv(8192)
                except socket.timeout:
                    break
                if not block:
                    break
                total += len(block)
                if total > MAX_RESPONSE:
                    raise GopherError("Response too large")
                chunks.append(block)
    except socket.gaierror as exc:
        raise GopherError(f"Unknown host: {host}") from exc
    except (TimeoutError, OSError) as exc:
        raise GopherError(f"Connection failed: {exc}") from exc
    return b"".join(chunks)


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def strip_text_terminator(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[-1].strip() == ".":
        lines = lines[:-1]
    return "\n".join(lines)


def parse_menu(text: str) -> list[GopherItem]:
    items: list[GopherItem] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            continue
        item_type = line[0]
        parts = line[1:].split("\t")
        if len(parts) < 3:
            continue
        name = parts[0]
        selector = parts[1] if len(parts) > 1 else ""
        host = parts[2] if len(parts) > 2 else ""
        port = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 70
        items.append(GopherItem(item_type, name, selector, host, port))
    return items


def looks_like_menu(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    hits = 0
    for line in lines[:20]:
        if len(line) < 4 or line[0] not in TYPE_LABELS or "\t" not in line:
            continue
        parts = line[1:].split("\t")
        if len(parts) >= 3:
            hits += 1
    return hits >= max(1, len(lines) // 2)


def parse_gopher_url(url: str) -> tuple[str, int, str, str | None]:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("gopher", ""):
        raise GopherError("URL must start with gopher://")
    host = parsed.hostname or ""
    if not host:
        raise GopherError("Missing host")
    port = parsed.port or 70
    selector = unquote(parsed.path or "")
    if parsed.query:
        selector = f"{selector}?{parsed.query}"
    search = None
    return host, port, selector, search


def proxy_href(
    host: str,
    port: int,
    selector: str,
    item_type: str,
    search: str | None = None,
    *,
    embed: bool = False,
) -> str:
    q = (
        f"host={quote(host, safe='')}"
        f"&port={port}"
        f"&selector={quote(selector, safe='')}"
        f"&type={quote(item_type, safe='')}"
    )
    if search:
        q += f"&search={quote(search, safe='')}"
    if embed:
        q += "&embed=1"
    return f"/browse?{q}"


def browse_href(gopher_url: str, *, embed: bool = False) -> str:
    q = f"url={quote(gopher_url, safe='')}"
    if embed:
        q += "&embed=1"
    return f"/browse?{q}"


def type_icon(item_type: str) -> str:
    return {
        "0": "📄",
        "1": "📁",
        "7": "🔍",
        "g": "🖼",
        "I": "🖼",
        "h": "🌐",
        "i": "ℹ",
        "s": "🔊",
        "8": "📟",
        "T": "📟",
    }.get(item_type, "•")


PAGE_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      --bg: #0f1410;
      --panel: #1a2318;
      --text: #c8d4b8;
      --accent: #7cb342;
      --link: #aed581;
      --muted: #6b7c5e;
      --border: #2e3d28;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Courier New", Courier, monospace;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
    }}
    header {{
      background: var(--panel);
      border-bottom: 1px solid var(--border);
      padding: 0.75rem 1rem;
    }}
    header h1 {{
      margin: 0 0 0.5rem;
      font-size: 1rem;
      color: var(--accent);
      letter-spacing: 0.05em;
    }}
    form.url-bar {{
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
    }}
    form.url-bar input[type="text"] {{
      flex: 1 1 16rem;
      min-width: 12rem;
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 0.45rem 0.6rem;
      font: inherit;
    }}
    form.url-bar button, .btn {{
      background: var(--accent);
      color: #0f1410;
      border: none;
      padding: 0.45rem 0.9rem;
      font: inherit;
      cursor: pointer;
      font-weight: bold;
    }}
    main {{
      padding: 1rem;
      max-width: 56rem;
    }}
    .location {{
      color: var(--muted);
      font-size: 0.85rem;
      margin-bottom: 1rem;
      word-break: break-all;
    }}
    .menu {{
      list-style: none;
      padding: 0;
      margin: 0;
    }}
    .menu li {{
      padding: 0.35rem 0;
      border-bottom: 1px solid var(--border);
    }}
    .menu li.info {{
      color: var(--muted);
      font-style: italic;
    }}
    .menu a {{
      color: var(--link);
      text-decoration: none;
    }}
    .menu a:hover {{ text-decoration: underline; }}
    .type-tag {{
      display: inline-block;
      width: 1.5rem;
      color: var(--muted);
    }}
    pre.text-content {{
      white-space: pre-wrap;
      word-break: break-word;
      background: var(--panel);
      border: 1px solid var(--border);
      padding: 1rem;
      overflow-x: auto;
    }}
    .search-box {{
      margin-top: 0.5rem;
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
    }}
    .search-box input {{
      flex: 1 1 12rem;
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 0.35rem 0.5rem;
      font: inherit;
    }}
    .error {{
      background: #3b1515;
      border: 1px solid #7a3030;
      color: #f5b8b8;
      padding: 0.75rem 1rem;
    }}
    .binary-note {{
      color: var(--muted);
      font-size: 0.9rem;
    }}
    img.inline {{
      max-width: 100%;
      border: 1px solid var(--border);
      background: #000;
    }}
    footer {{
      margin-top: 2rem;
      color: var(--muted);
      font-size: 0.8rem;
    }}
  </style>
</head>
<body>
  <header>
    <h1>GOPHER PROXY</h1>
    <form class="url-bar" method="get" action="/browse">
      <input type="text" name="url" placeholder="gopher://sdf.org/" value="{url_value}">
      <button type="submit">Go</button>
    </form>
  </header>
  <main>
    {body}
  </main>
  <footer>
    Serving Gopher over HTTP — links stay inside this proxy.
  </footer>
</body>
</html>
"""

EMBED_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      --bg: #0f1410;
      --panel: #1a2318;
      --text: #c8d4b8;
      --accent: #7cb342;
      --link: #aed581;
      --muted: #6b7c5e;
      --border: #2e3d28;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Courier New", Courier, monospace;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
    }}
    header {{
      background: var(--panel);
      border-bottom: 1px solid var(--border);
      padding: 0.4rem 0.6rem;
    }}
    form.url-bar {{
      display: flex;
      gap: 0.4rem;
    }}
    form.url-bar input[type="text"] {{
      flex: 1;
      min-width: 0;
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 0.35rem 0.5rem;
      font: inherit;
      font-size: 0.85rem;
    }}
    form.url-bar button, .btn {{
      background: var(--accent);
      color: #0f1410;
      border: none;
      padding: 0.35rem 0.65rem;
      font: inherit;
      font-size: 0.85rem;
      cursor: pointer;
      font-weight: bold;
    }}
    main {{
      padding: 0.75rem;
    }}
    .location {{
      color: var(--muted);
      font-size: 0.8rem;
      margin-bottom: 0.75rem;
      word-break: break-all;
    }}
    .menu {{
      list-style: none;
      padding: 0;
      margin: 0;
    }}
    .menu li {{
      padding: 0.3rem 0;
      border-bottom: 1px solid var(--border);
    }}
    .menu li.info {{
      color: var(--muted);
      font-style: italic;
    }}
    .menu a {{
      color: var(--link);
      text-decoration: none;
    }}
    .menu a:hover {{ text-decoration: underline; }}
    .type-tag {{
      display: inline-block;
      width: 1.5rem;
      color: var(--muted);
    }}
    pre.text-content {{
      white-space: pre-wrap;
      word-break: break-word;
      background: var(--panel);
      border: 1px solid var(--border);
      padding: 0.75rem;
      overflow-x: auto;
      font-size: 0.9rem;
    }}
    .search-box {{
      margin-top: 0.4rem;
      display: flex;
      gap: 0.4rem;
      flex-wrap: wrap;
    }}
    .search-box input {{
      flex: 1 1 10rem;
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 0.3rem 0.45rem;
      font: inherit;
      font-size: 0.85rem;
    }}
    .error {{
      background: #3b1515;
      border: 1px solid #7a3030;
      color: #f5b8b8;
      padding: 0.6rem 0.75rem;
    }}
    .binary-note {{
      color: var(--muted);
      font-size: 0.85rem;
    }}
    img.inline {{
      max-width: 100%;
      border: 1px solid var(--border);
      background: #000;
    }}
  </style>
</head>
<body>
  <header>
    <form class="url-bar" method="get" action="/browse">
      <input type="hidden" name="embed" value="1">
      <input type="text" name="url" placeholder="gopher://host/path" value="{url_value}">
      <button type="submit">Go</button>
    </form>
  </header>
  <main>
    {body}
  </main>
</body>
</html>
"""


def render_page(title: str, url_value: str, body: str, *, embed: bool = False) -> str:
    shell = EMBED_SHELL if embed else PAGE_SHELL
    return shell.format(
        title=html.escape(title),
        url_value=html.escape(url_value, quote=True),
        body=body,
    )


def render_menu(items: list[GopherItem], location: str, *, embed: bool = False) -> str:
    rows: list[str] = []
    for item in items:
        if item.type == "i":
            rows.append(f'<li class="info">{html.escape(item.name)}</li>')
            continue
        if item.type == "7":
            rows.append(
                f'<li>'
                f'<span class="type-tag">{type_icon(item.type)}</span>'
                f'<strong>{html.escape(item.name)}</strong>'
                f'<form class="search-box" method="get" action="/browse">'
                f'<input type="hidden" name="host" value="{html.escape(item.host, quote=True)}">'
                f'<input type="hidden" name="port" value="{item.port}">'
                f'<input type="hidden" name="selector" value="{html.escape(item.selector, quote=True)}">'
                f'<input type="hidden" name="type" value="7">'
                + ('<input type="hidden" name="embed" value="1">' if embed else "")
                + f'<input type="search" name="search" placeholder="Search terms" required>'
                f'<button type="submit" class="btn">Search</button>'
                f'</form>'
                f'</li>'
            )
            continue
        href = proxy_href(item.host, item.port, item.selector, item.type, embed=embed)
        label = TYPE_LABELS.get(item.type, item.type)
        rows.append(
            f'<li>'
            f'<span class="type-tag" title="{html.escape(label)}">{type_icon(item.type)}</span>'
            f'<a href="{href}">{html.escape(item.name)}</a>'
            f'</li>'
        )
    return (
        f'<div class="location">📍 {html.escape(location)}</div>'
        f'<ul class="menu">{"".join(rows)}</ul>'
    )


def render_text(content: str, location: str) -> str:
    return (
        f'<div class="location">📍 {html.escape(location)}</div>'
        f'<pre class="text-content">{html.escape(content)}</pre>'
    )


def render_binary(location: str, mime: str, data: bytes, item_type: str) -> str:
    if item_type == "g" or (item_type == "I" and mime.startswith("image/")):
        import base64

        b64 = base64.b64encode(data).decode("ascii")
        return (
            f'<div class="location">📍 {html.escape(location)}</div>'
            f'<img class="inline" src="data:{mime};base64,{b64}" alt="Gopher image">'
        )
    size = len(data)
    return (
        f'<div class="location">📍 {html.escape(location)}</div>'
        f'<p class="binary-note">Binary content ({size:,} bytes, type {html.escape(item_type)}).</p>'
        f'<p class="binary-note">This proxy displays text and images inline; other binary types are not rendered.</p>'
    )


def browse(
    host: str,
    port: int,
    selector: str,
    item_type: str = "1",
    search: str | None = None,
    *,
    embed: bool = False,
) -> str:
    location = f"gopher://{host}:{port}{selector}"
    if search:
        location += f" ? {search}"

    data = fetch_gopher(host, port, selector, search)
    if not data:
        return render_page("Empty", location, render_text("(empty response)", location), embed=embed)

    if item_type in MENU_TYPES or (item_type == "" and looks_like_menu(decode_text(data))):
        text = decode_text(data)
        items = parse_menu(text)
        if items:
            body = render_menu(items, location, embed=embed)
            return render_page(f"{host} — Gopher Menu", location, body, embed=embed)

    if item_type in TEXT_TYPES or item_type in ("h",):
        text = strip_text_terminator(decode_text(data))
        body = render_text(text, location)
        return render_page(f"{host} — Text", location, body, embed=embed)

    if item_type in BINARY_TYPES or item_type in ("g", "I"):
        mime = "image/gif" if item_type == "g" else "application/octet-stream"
        if item_type == "I":
            mime = "image/png"
        body = render_binary(location, mime, data, item_type)
        return render_page(f"{host} — Binary", location, body, embed=embed)

    text = decode_text(data)
    if looks_like_menu(text):
        items = parse_menu(text)
        body = render_menu(items, location, embed=embed)
        return render_page(f"{host} — Gopher Menu", location, body, embed=embed)

    text = strip_text_terminator(text)
    body = render_text(text, location)
    return render_page(f"{host} — Gopher", location, body, embed=embed)


class Handler(BaseHTTPRequestHandler):
    server_version = "GopherProxy/1.0"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def send_html(self, status: int, content: str, *, embed: bool = False) -> None:
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if embed:
            # Allow GitHub Pages (or any parent) to frame the proxy UI.
            self.send_header("Content-Security-Policy", "frame-ancestors *")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        embed = False
        try:
            params = parse_qs(parsed.query)
            get1 = lambda k, d="": (params.get(k, [d])[0] or d)
            embed = get1("embed").lower() in ("1", "true", "yes") or parsed.path == "/embed"

            if parsed.path in ("/", "/index.html"):
                body = render_page(
                    "Gopher Proxy",
                    DEFAULT_GOPHER,
                    '<p class="binary-note">Enter a <code>gopher://</code> URL above, or try '
                    f'<a href="{browse_href("gopher://gopher.floodgap.com/")}">gopher.floodgap.com</a>, '
                    f'<a href="{browse_href(DEFAULT_GOPHER)}">your SDF hole</a>. '
                    f'Embed with <a href="/embed?url={quote(DEFAULT_GOPHER, safe="")}">/embed</a>.</p>',
                )
                self.send_html(200, body)
                return

            if parsed.path == "/embed":
                start_url = get1("url", DEFAULT_GOPHER)
                host, port, selector, _ = parse_gopher_url(start_url)
                page = browse(host, port, selector, "1", None, embed=True)
                self.send_html(200, page, embed=True)
                return

            if parsed.path != "/browse":
                self.send_html(404, render_page("Not Found", "", '<p class="error">Not found.</p>'))
                return

            if url := get1("url"):
                host, port, selector, _ = parse_gopher_url(url)
                item_type = "1"
                search = None
            else:
                host = get1("host")
                port = int(get1("port", "70") or "70")
                selector = get1("selector")
                item_type = get1("type", "1")
                search = get1("search") or None

            if not host:
                raise GopherError("Missing host")

            page = browse(host, port, selector, item_type, search, embed=embed)
            self.send_html(200, page, embed=embed)
        except GopherError as exc:
            self.send_html(
                400,
                render_page("Error", "", f'<p class="error">{html.escape(str(exc))}</p>', embed=embed),
                embed=embed,
            )
        except Exception as exc:
            self.send_html(
                500,
                render_page("Error", "", f'<p class="error">{html.escape(str(exc))}</p>', embed=embed),
                embed=embed,
            )


def main() -> None:
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    else:
        port = int(os.environ.get("PORT", DEFAULT_PORT))
    server = ThreadingHTTPServer((BIND_HOST, port), Handler)
    print(f"Gopher proxy running on {BIND_HOST}:{port}")
    print(f"  Standalone: http://127.0.0.1:{port}/")
    print(f"  Embed URL:  http://127.0.0.1:{port}/embed?url={quote(DEFAULT_GOPHER, safe='')}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()