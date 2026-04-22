"""Shared Feishu render helpers used by runtime and workspace callers.

This module owns render-shape decisions only. It must not talk to Feishu
transport APIs directly.
"""

from __future__ import annotations

import json
import re

_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_BOLD_UNDERSCORE_RE = re.compile(r"__(.+?)__")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_MD_STRIKE_RE = re.compile(r"~~(.+?)~~")
_TABLE_RE = re.compile(r"((?:^\|.+\|\s*$\n?)+)", re.MULTILINE)
_CODE_BLOCK_RE = re.compile(r"(```[\s\S]*?```)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

_COMPLEX_MD_RE = re.compile(
    r"```"
    r"|^\|.+\|.*\n\s*\|[-:\s|]+\|"
    r"|^#{1,6}\s+",
    re.MULTILINE,
)
_SIMPLE_MD_RE = re.compile(
    r"\*\*.+?\*\*"
    r"|__.+?__"
    r"|(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)"
    r"|~~.+?~~",
    re.DOTALL,
)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")
_LIST_RE = re.compile(r"^[\s]*[-*+]\s+", re.MULTILINE)
_OLIST_RE = re.compile(r"^[\s]*\d+\.\s+", re.MULTILINE)

_TEXT_MAX_LEN = 200
_POST_MAX_LEN = 2000
STREAM_ELEMENT_ID = "stream_content"


def normalize_visible_newlines(text: str) -> str:
    """Treat escaped newline sequences as visible line breaks for user-facing content."""
    normalized = str(text or "")
    return normalized.replace("\\r\\n", "\n").replace("\\n", "\n")


def strip_md_formatting(text: str) -> str:
    """Strip markdown markers for plain-display Feishu cells."""
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_BOLD_UNDERSCORE_RE.sub(r"\1", text)
    text = _MD_ITALIC_RE.sub(r"\1", text)
    text = _MD_STRIKE_RE.sub(r"\1", text)
    return text


def parse_md_table(table_text: str) -> dict | None:
    """Parse a markdown table into a Feishu table element."""
    lines = [_line.strip() for _line in table_text.strip().split("\n") if _line.strip()]
    if len(lines) < 3:
        return None

    def split(_line: str) -> list[str]:
        return [cell.strip() for cell in _line.strip("|").split("|")]

    headers = [strip_md_formatting(header) for header in split(lines[0])]
    rows = [[strip_md_formatting(cell) for cell in split(_line)] for _line in lines[2:]]
    columns = [
        {"tag": "column", "name": f"c{i}", "display_name": header, "width": "auto"}
        for i, header in enumerate(headers)
    ]
    return {
        "tag": "table",
        "page_size": len(rows) + 1,
        "columns": columns,
        "rows": [{f"c{i}": row[i] if i < len(row) else "" for i in range(len(headers))} for row in rows],
    }


def split_headings(content: str) -> list[dict]:
    """Split markdown headings into Feishu div elements."""
    protected = content
    code_blocks: list[str] = []
    for match in _CODE_BLOCK_RE.finditer(content):
        code_blocks.append(match.group(1))
        protected = protected.replace(match.group(1), f"\x00CODE{len(code_blocks)-1}\x00", 1)

    elements: list[dict] = []
    last_end = 0
    for match in _HEADING_RE.finditer(protected):
        before = protected[last_end:match.start()].strip()
        if before:
            elements.append({"tag": "markdown", "content": before})
        text = strip_md_formatting(match.group(2).strip())
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{text}**" if text else "",
                },
            }
        )
        last_end = match.end()

    remaining = protected[last_end:].strip()
    if remaining:
        elements.append({"tag": "markdown", "content": remaining})

    for i, code_block in enumerate(code_blocks):
        token = f"\x00CODE{i}\x00"
        for element in elements:
            if element.get("tag") == "markdown":
                element["content"] = element["content"].replace(token, code_block)

    return elements or [{"tag": "markdown", "content": content}]


def build_card_elements(content: str) -> list[dict]:
    """Split markdown content into Feishu card elements."""
    elements: list[dict] = []
    last_end = 0
    for match in _TABLE_RE.finditer(content):
        before = content[last_end:match.start()]
        if before.strip():
            elements.extend(split_headings(before))
        elements.append(parse_md_table(match.group(1)) or {"tag": "markdown", "content": match.group(1)})
        last_end = match.end()
    remaining = content[last_end:]
    if remaining.strip():
        elements.extend(split_headings(remaining))
    return elements or [{"tag": "markdown", "content": content}]


def split_elements_by_table_limit(elements: list[dict], max_tables: int = 1) -> list[list[dict]]:
    """Split Feishu card elements so each group respects the table-per-card limit."""
    if not elements:
        return [[]]

    groups: list[list[dict]] = []
    current: list[dict] = []
    table_count = 0
    for element in elements:
        if element.get("tag") == "table":
            if table_count >= max_tables:
                if current:
                    groups.append(current)
                current = []
                table_count = 0
            current.append(element)
            table_count += 1
        else:
            current.append(element)
    if current:
        groups.append(current)
    return groups or [[]]


def detect_msg_format(content: str) -> str:
    """Return the preferred Feishu message format for content."""
    stripped = content.strip()
    if _COMPLEX_MD_RE.search(stripped):
        return "interactive"
    if len(stripped) > _POST_MAX_LEN:
        return "interactive"
    if _SIMPLE_MD_RE.search(stripped):
        return "interactive"
    if _LIST_RE.search(stripped) or _OLIST_RE.search(stripped):
        return "interactive"
    if _MD_LINK_RE.search(stripped):
        return "post"
    if len(stripped) <= _TEXT_MAX_LEN:
        return "text"
    return "post"


def markdown_to_post(content: str) -> str:
    """Convert markdown links/plain text into Feishu post JSON."""
    lines = content.strip().split("\n")
    paragraphs: list[list[dict]] = []
    for line in lines:
        elements: list[dict] = []
        last_end = 0
        for match in _MD_LINK_RE.finditer(line):
            before = line[last_end:match.start()]
            if before:
                elements.append({"tag": "text", "text": before})
            elements.append({"tag": "a", "text": match.group(1), "href": match.group(2)})
            last_end = match.end()
        remaining = line[last_end:]
        if remaining:
            elements.append({"tag": "text", "text": remaining})
        if not elements:
            elements.append({"tag": "text", "text": ""})
        paragraphs.append(elements)
    return json.dumps({"zh_cn": {"content": paragraphs}}, ensure_ascii=False)


def build_interactive_card_content(
    content: str,
    *,
    title: str | None = None,
    template: str | None = None,
    mention_user_id: str | None = None,
    mention_all: bool = False,
) -> str:
    """Build interactive-card JSON content from shared rendering helpers."""
    stripped = normalize_visible_newlines(content).strip()
    mention_prefix = ""
    if mention_all:
        mention_prefix = "<at id=all></at> "
    elif mention_user_id:
        mention_prefix = f"<at id={mention_user_id}></at> "
    body = f"{mention_prefix}{stripped}".strip()

    card: dict[str, object] = {
        "config": {"wide_screen_mode": True},
        "elements": build_card_elements(body),
    }
    if title:
        header: dict[str, object] = {"title": {"tag": "plain_text", "content": title}}
        if template:
            header["template"] = template
        card["header"] = header
    return json.dumps(card, ensure_ascii=False)


def build_interactive_card_payload(
    content: str,
    *,
    title: str | None = None,
    header_icon: str = "📌",
    mention_user_id: str | None = None,
    mention_all: bool = False,
) -> dict[str, str]:
    """Build a user-visible interactive Feishu card payload."""
    stripped = (content or "").strip()
    if title:
        header_title = title
        template = "purple"
    elif stripped.startswith("❌"):
        header_title = "❌ 消息"
        template = "red"
    else:
        header_title = f"{header_icon} 消息"
        template = "purple"
    return {
        "msg_type": "interactive",
        "content": build_interactive_card_content(
            stripped,
            title=header_title,
            template=template,
            mention_user_id=mention_user_id,
            mention_all=mention_all,
        ),
    }


def build_streaming_placeholder_card_json(placeholder_text: str) -> str:
    """Build the initial CardKit streaming placeholder payload."""
    normalized_placeholder = normalize_visible_newlines(placeholder_text)
    card_json = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "update_multi": True, "streaming_mode": True},
        "body": {"elements": [{"tag": "markdown", "content": normalized_placeholder, "element_id": STREAM_ELEMENT_ID}]},
    }
    return json.dumps(card_json, ensure_ascii=False)
