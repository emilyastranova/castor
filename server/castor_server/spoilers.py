"""Spoiler text, Discord style: `||hidden||` in titles, descriptions, comments, posts and notes.
The web shows it as a bar until clicked (web `lib/spoilers.ts`: keep the two rules alike). Where
text can't be hidden (chat alerts, webhooks), `mask` censors it with asterisks; the timeline
export (report.py) draws a bar that shows the text on hover. Spoilers hide things from a glance,
not from anyone determined: the text is in the API, search and exports. Secrets go in the vault."""

from __future__ import annotations

import html
import re
from typing import Any

SPOILER = re.compile(r"\|\|(?=[^\s|])((?:[^|\n]|\|(?!\|))*?[^\s|])\|\|")


def mask(text: str | None) -> str:
    """Each spoiler censored with asterisks (3 to 8, about half its length, so it doesn't give the
    length away); the pipes never go out."""
    return SPOILER.sub(lambda m: "*" * max(3, min(8, round(len(m.group(1)) / 2))), text or "")


def html_text(text: str | None) -> str:
    """Plain text as HTML, its spoilers in `<span class="spoiler">`."""
    out, at = [], 0
    source = text or ""
    for m in SPOILER.finditer(source):
        out.append(html.escape(source[at:m.start()]))
        out.append(f'<span class="spoiler">{html.escape(m.group(1))}</span>')
        at = m.end()
    out.append(html.escape(source[at:]))
    return "".join(out)


def markdown_it_plugin(md: Any) -> None:
    """`||text||` → `<span class="spoiler">…</span>` in markdown-it (inline markdown inside works)."""

    def rule(state: Any, silent: bool) -> bool:
        if not state.src.startswith("||", state.pos):
            return False
        m = SPOILER.match(state.src, state.pos)
        if m is None:
            return False
        if not silent:
            opening = state.push("spoiler_open", "span", 1)
            opening.attrSet("class", "spoiler")
            old_pos, old_max = state.pos, state.posMax
            state.pos, state.posMax = state.pos + 2, m.end() - 2
            state.md.inline.tokenize(state)
            state.pos, state.posMax = old_pos, old_max
            state.push("spoiler_close", "span", -1)
        state.pos = m.end()
        return True

    md.inline.ruler.before("emphasis", "spoiler", rule)
    # Plain text runs until a character some rule reacts to: `|` has to be one, or "a ||b||" is
    # read as text before the rule above sees the pipes.
    if hasattr(md.inline, "add_terminator_char"):  # markdown-it-py 4
        md.inline.add_terminator_char("|")
    else:
        stops = re.compile(r"[\n!#$%&*+\-:<=>@\[\\\]^_`{}~|]")

        def text(state: Any, silent: bool) -> bool:
            found = stops.search(state.src, state.pos, state.posMax)
            end = found.start() if found else state.posMax
            if end == state.pos:
                return False
            if not silent:
                state.pending += state.src[state.pos:end]
            state.pos = end
            return True

        md.inline.ruler.at("text", text)
