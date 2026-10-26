"""Build the self-contained submission files from their sources.

    .venv/bin/python docs/build/build_artifacts.py

- docs/build/report.src.html -> report.html
- docs/build/slides.src.html -> slides.html

Every <img src="docs/media/..."> is inlined as a base64 data URI. In the slides,
<!--REVEAL_CSS--> and <!--REVEAL_JS--> are replaced with the vendored Reveal.js 5.2.1
files in docs/vendor/reveal/ (MIT licence), so both outputs open offline as single files.
Replace a screenshot in docs/media/ and rerun this script to update both files.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "docs" / "vendor" / "reveal"


def _inline_images(html: str) -> str:
    def repl(m: re.Match) -> str:
        path = ROOT / m.group(2)
        if not path.exists():
            raise FileNotFoundError(f"missing image: {path}")
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f'{m.group(1)}data:image/png;base64,{data}"'
    return re.sub(r'(src=")(docs/media/[\w\-]+\.png)"', repl, html)


def _inline_reveal(html: str) -> str:
    css = (VENDOR / "reset.css").read_text() + "\n" + (VENDOR / "reveal.css").read_text()
    js = (VENDOR / "reveal.js").read_text()
    html = html.replace("<!--REVEAL_CSS-->", f"<style>/* Reveal.js 5.2.1, MIT licence */\n{css}</style>")
    return html.replace("<!--REVEAL_JS-->", f"<script>/* Reveal.js 5.2.1, MIT licence */\n{js}</script>")


def main() -> None:
    for src, out, reveal in (("report.src.html", "report.html", False), ("slides.src.html", "slides.html", True)):
        p = ROOT / "docs" / "build" / src
        if not p.exists():
            continue
        html = _inline_images(p.read_text(encoding="utf-8"))
        if reveal:
            html = _inline_reveal(html)
        (ROOT / out).write_text(html, encoding="utf-8")
        print(f"wrote {out} ({len(html) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
