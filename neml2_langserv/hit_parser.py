"""Tolerant HIT document context parser.

Uses only bracket delimiters ([name] / []) — no indentation logic.
Bracket depth:  0 = file root, 1 = top-level section, 2 = sub-block.
"""

import re
from dataclasses import dataclass, field

_OPEN = re.compile(r"^\s*\[([^\]/][^\]]*)\]\s*(?:#.*)?$")
_CLOSE = re.compile(r"^\s*\[\]\s*(?:#.*)?$")
_KV = re.compile(r"^\s*(\w[\w/]*)\s*[:=]=?\s*(.*?)\s*(?:#.*)?$")


@dataclass
class BlockContext:
    section: str = ""
    block_name: str = ""
    block_type: str = ""
    options_set: set[str] = field(default_factory=set)


def get_context(lines: list[str], cursor_line: int) -> BlockContext | None:
    """Return the HIT context at *cursor_line* (0-indexed)."""
    depth = 0
    ctx = BlockContext()

    for i, line in enumerate(lines):
        if i > cursor_line:
            break

        close = _CLOSE.match(line)
        if close:
            if depth == 2:
                ctx.block_name = ""
                ctx.block_type = ""
                ctx.options_set = set()
            if depth == 1:
                ctx.section = ""
            depth = max(0, depth - 1)
            continue

        open_m = _OPEN.match(line)
        if open_m:
            name = open_m.group(1).strip()
            if depth == 0:
                ctx.section = name
                depth = 1
            elif depth == 1:
                ctx.block_name = name
                ctx.block_type = ""
                ctx.options_set = set()
                depth = 2
            continue

        if depth == 2:
            kv = _KV.match(line)
            if kv:
                key, val = kv.group(1), kv.group(2).strip()
                if key == "type":
                    ctx.block_type = val.strip("'\"")
                else:
                    ctx.options_set.add(key)

    if depth < 2:
        return None
    return ctx


@dataclass
class ParsedBlock:
    context: BlockContext
    start_line: int
    end_line: int


def parse_all_blocks(lines: list[str]) -> list[ParsedBlock]:
    """Return every sub-block found in the document."""
    blocks: list[ParsedBlock] = []
    depth = 0
    section = ""
    block_name = ""
    block_type = ""
    options_set: set[str] = set()
    block_start = 0

    for i, line in enumerate(lines):
        close = _CLOSE.match(line)
        if close:
            if depth == 2:
                blocks.append(
                    ParsedBlock(
                        BlockContext(section, block_name, block_type, options_set),
                        block_start,
                        i,
                    )
                )
                block_name = ""
                block_type = ""
                options_set = set()
            elif depth == 1:
                section = ""
            depth = max(0, depth - 1)
            continue

        open_m = _OPEN.match(line)
        if open_m:
            name = open_m.group(1).strip()
            if depth == 0:
                section = name
                depth = 1
            elif depth == 1:
                block_name = name
                block_type = ""
                options_set = set()
                block_start = i
                depth = 2
            continue

        if depth == 2:
            kv = _KV.match(line)
            if kv:
                key, val = kv.group(1), kv.group(2).strip()
                if key == "type":
                    block_type = val.strip("'\"")
                else:
                    options_set.add(key)

    return blocks
