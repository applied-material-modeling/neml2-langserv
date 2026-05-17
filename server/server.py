import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

import nmhit
from lsprotocol import types as lsp
from pygls.lsp.server import LanguageServer

from .hit_parser import get_context, parse_all_blocks
from .syntax_client import NEML2_MIN_VERSION, NMHIT_MIN_VERSION, get_client

server = LanguageServer("neml2-ls", "v0.1")

def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split(".")[:3])


def _check_pkg(pkg: str, min_ver: str) -> str | None:
    """Return a warning string if pkg is missing or below min_ver, else None."""
    try:
        installed = _pkg_version(pkg)
        if _version_tuple(installed) < _version_tuple(min_ver):
            return (
                f"'{pkg}' {installed} is installed but >={min_ver} is required. "
                "Some features may not work correctly."
            )
    except PackageNotFoundError:
        return (
            f"'{pkg}' is not installed (>={min_ver} required). "
            "Please install it in the active Python environment."
        )
    return None


@server.feature(lsp.INITIALIZED)
def _on_initialized(ls: LanguageServer, params: lsp.InitializedParams) -> None:
    for pkg, min_ver, severity in [
        ("neml2", NEML2_MIN_VERSION, lsp.MessageType.Error),
        ("nmhit", NMHIT_MIN_VERSION, lsp.MessageType.Warning),
    ]:
        msg = _check_pkg(pkg, min_ver)
        if msg:
            ls.window_show_message(
                lsp.ShowMessageParams(type=severity, message=f"NEML2: {msg}")
            )

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_FTYPE_KIND = {
    "PARAMETER": lsp.CompletionItemKind.Variable,
    "INPUT": lsp.CompletionItemKind.Field,
    "OUTPUT": lsp.CompletionItemKind.Field,
    "BUFFER": lsp.CompletionItemKind.Variable,
    "NONE": lsp.CompletionItemKind.Property,
}

_TYPE_ASSIGN = re.compile(r"^\s*type\s*[:=]=?\s*\S*$")
_KEY_POS = re.compile(r"^\s*[\w/]*$")


def _doc_lines(lines: list[str]) -> list[str]:
    """Return document lines (handles both list[str] and str)."""
    return lines


def _word_at(line: str, character: int) -> str:
    start = character
    while start > 0 and (line[start - 1].isalnum() or line[start - 1] in "_:/"):
        start -= 1
    end = character
    while end < len(line) and (line[end].isalnum() or line[end] in "_:/"):
        end += 1
    return line[start:end]


def _fmt_option(opt: dict) -> str:
    ftype = opt.get("ftype", "")
    header = f"**{opt['name']}**" + (f"  `{ftype}`" if ftype and ftype != "NONE" else "")
    lines = [header]
    if opt.get("type"):
        lines.append(f"*Type:* `{opt['type']}`")
    if opt.get("required"):
        lines.append("*Required*")
    if opt.get("doc"):
        lines.append("")
        lines.append(opt["doc"])
    return "\n".join(lines)


def _get_lines(ls: LanguageServer, uri: str) -> list[str]:
    doc = ls.workspace.get_text_document(uri)
    return doc.source.splitlines()


# ---------------------------------------------------------------------------
# completion
# ---------------------------------------------------------------------------


@server.feature(
    lsp.TEXT_DOCUMENT_COMPLETION,
    lsp.CompletionOptions(trigger_characters=["=", " ", "\t"]),
)
def completions(
    ls: LanguageServer, params: lsp.CompletionParams
) -> list[lsp.CompletionItem]:
    lines = _get_lines(ls, params.text_document.uri)
    line_idx = params.position.line
    if line_idx >= len(lines):
        return []

    current_line = lines[line_idx]
    ctx = get_context(lines, line_idx)
    if ctx is None:
        return []

    syntax = get_client()
    if syntax is None:
        return []

    # After `type =` → offer all registered types for this section
    if _TYPE_ASSIGN.match(current_line):
        try:
            types = syntax.list_types(ctx.section)
        except Exception:
            return []
        return [
            lsp.CompletionItem(
                label=t["type"],
                kind=lsp.CompletionItemKind.Class,
                detail=t["section"],
                documentation=lsp.MarkupContent(
                    kind=lsp.MarkupKind.PlainText, value=t.get("doc", "")
                ),
            )
            for t in types
        ]

    # At an option key position → offer option names for the current type
    if ctx.block_type and _KEY_POS.match(current_line):
        try:
            info = syntax.get_options(ctx.block_type)
        except Exception:
            return []
        if not info:
            return []
        remaining = [o for o in info["options"] if o["name"] not in ctx.options_set]
        return [
            lsp.CompletionItem(
                label=o["name"],
                kind=_FTYPE_KIND.get(o["ftype"], lsp.CompletionItemKind.Property),
                detail=o.get("type") or "",
                documentation=lsp.MarkupContent(
                    kind=lsp.MarkupKind.Markdown, value=_fmt_option(o)
                ),
            )
            for o in remaining
        ]

    return []


# ---------------------------------------------------------------------------
# hover
# ---------------------------------------------------------------------------


@server.feature(lsp.TEXT_DOCUMENT_HOVER)
def hover(ls: LanguageServer, params: lsp.HoverParams) -> lsp.Hover | None:
    lines = _get_lines(ls, params.text_document.uri)
    line_idx = params.position.line
    if line_idx >= len(lines):
        return None

    word = _word_at(lines[line_idx], params.position.character)
    if not word:
        return None

    ctx = get_context(lines, line_idx)
    syntax = get_client()
    if syntax is None:
        return None

    # Hover on a type name
    try:
        info = syntax.get_options(word)
        if info:
            md = f"**{word}** _{info['section']}_\n\n{info.get('doc', '')}"
            return lsp.Hover(
                contents=lsp.MarkupContent(kind=lsp.MarkupKind.Markdown, value=md)
            )
    except Exception:
        pass

    # Hover on an option key within a typed block
    if ctx and ctx.block_type:
        try:
            info = syntax.get_options(ctx.block_type)
            if info:
                by_name = {o["name"]: o for o in info["options"]}
                if word in by_name:
                    return lsp.Hover(
                        contents=lsp.MarkupContent(
                            kind=lsp.MarkupKind.Markdown,
                            value=_fmt_option(by_name[word]),
                        )
                    )
        except Exception:
            pass

    return None


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------


@server.feature(lsp.TEXT_DOCUMENT_FORMATTING)
def formatting(
    ls: LanguageServer, params: lsp.DocumentFormattingParams
) -> list[lsp.TextEdit] | None:
    doc = ls.workspace.get_text_document(params.text_document.uri)
    source = doc.source
    try:
        root = nmhit.parse_text(source)
        formatted = root.render(indent=0, indent_text="  ")
    except Exception:
        return None

    lines = source.splitlines()
    last_line = len(lines) - 1
    last_char = len(lines[last_line]) if lines else 0
    whole_doc = lsp.Range(
        start=lsp.Position(line=0, character=0),
        end=lsp.Position(line=last_line, character=last_char),
    )
    return [lsp.TextEdit(range=whole_doc, new_text=formatted)]
