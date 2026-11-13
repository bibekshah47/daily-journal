"""Sanitize journal HTML and derive searchable plain text."""
from html.parser import HTMLParser
import re

import nh3
from markupsafe import Markup

TAGS = {"p", "br", "strong", "b", "em", "i", "u", "h2", "h3", "ol", "ul", "li", "blockquote", "a", "span"}


# Keep in sync with the small Quill palette in static/journal-editor.js.
HIGHLIGHT_COLORS = {"#fff2b2", "#d9ead3", "#d9eaf7", "#eadcf4", "#fce1d6"}
HIGHLIGHT_VALUES = {color: color for color in HIGHLIGHT_COLORS}
for color in HIGHLIGHT_COLORS:
    rgb = tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))
    HIGHLIGHT_VALUES[f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"] = color


def filter_attribute(tag, attribute, value):
    if attribute != "style":
        return value
    # Accept exactly one approved background-color declaration on spans.
    # Other CSS, multiple declarations, URLs, variables and !important are rejected.
    match = re.fullmatch(r"\s*background-color\s*:\s*([^;]+)\s*;?\s*", value, re.I)
    if tag != "span" or not match:
        return None
    color = HIGHLIGHT_VALUES.get(re.sub(r"\s+", "", match[1]).lower())
    return f"background-color: {color};" if color else None


def clean_html(value):
    return nh3.clean(
        value or "", tags=TAGS, attributes={"a": {"href"}, "span": {"style"}},
        attribute_filter=filter_attribute,
        clean_content_tags={"script", "style", "iframe", "object"},
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer", strip_comments=True,
    )


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"p", "h2", "h3", "li", "blockquote"}:
            self.parts.append("\n")


def prepare_content(form):
    if form.get("editor_format") == "html":
        html = clean_html(form.get("journal_html", ""))
        parser = TextExtractor()
        parser.feed(html)
        return "".join(parser.parts).strip(), html
    return form.get("journal", "").strip(), None


def safe_journal_html(value):
    # Sanitize again on output, including content inserted outside the app.
    return Markup(clean_html(value))
