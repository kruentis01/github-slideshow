"""Small text helpers: HTML to plain text, sentence splitting."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "tr", "table", "section", "article", "header", "footer",
}
_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "nav", "footer", "form"}
_VOID_TAGS = {"br", "img", "input", "meta", "link", "hr", "source", "area", "col", "wbr"}


class _TextExtractor(HTMLParser):
    """Collects visible text. If ``focus`` is given, only text inside the first
    element whose class or id matches it is kept."""

    def __init__(self, focus: re.Pattern | None = None):
        super().__init__(convert_charrefs=True)
        self.focus = focus
        self.parts: list[str] = []
        self.skip_depth = 0
        self.focus_depth = 0  # >0 while inside the focused element
        self.focus_done = False

    def handle_starttag(self, tag, attrs):
        if tag in _VOID_TAGS:
            if tag == "br":
                self.parts.append("\n")
            return
        if tag in _SKIP_TAGS or self.skip_depth:
            self.skip_depth += 1
            return
        if self.focus is not None and not self.focus_done:
            if self.focus_depth:
                self.focus_depth += 1
            else:
                attr = dict(attrs)
                marker = f"{attr.get('class', '')} {attr.get('id', '')}"
                if self.focus.search(marker):
                    self.focus_depth = 1
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("• ")

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS:
            return
        if self.skip_depth:
            self.skip_depth -= 1
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if self.focus is not None and self.focus_depth:
            self.focus_depth -= 1
            if self.focus_depth == 0:
                self.focus_done = True

    def handle_data(self, data):
        if self.skip_depth:
            return
        if self.focus is not None and not self.focus_depth:
            return
        self.parts.append(data)


def html_to_text(markup: str, focus: str | None = None) -> str:
    """Convert HTML to readable plain text. Plain text passes through unchanged."""
    if not markup:
        return ""
    if "<" not in markup:
        return normalise_whitespace(html.unescape(markup))
    parser = _TextExtractor(re.compile(focus, re.I) if focus else None)
    parser.feed(markup)
    parser.close()
    return normalise_whitespace("".join(parser.parts))


def normalise_whitespace(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sentences(text: str) -> list[str]:
    """Split text into sentences, treating line breaks as boundaries too."""
    out: list[str] = []
    for chunk in re.split(r"\n+", text):
        chunk = chunk.strip(" •-*\t")
        if not chunk:
            continue
        for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9£])", chunk):
            sent = sent.strip()
            if sent:
                out.append(sent)
    return out


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:- ")
    return cut + "…"
