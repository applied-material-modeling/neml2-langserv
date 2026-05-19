import os
import re
import tempfile
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import nmhit
from lsprotocol import types as lsp
from pygls.lsp.server import LanguageServer

from .hit_parser import get_context, parse_all_blocks
from .inspect_client import probe_inspect, run_inspect
from .syntax_client import NEML2_MIN_VERSION, NMHIT_MIN_VERSION, get_client

# Reminder: the version string below is duplicated in /pyproject.toml and
# /client/package.json. Keep all three in sync until we wire up a single
# source of truth.
server = LanguageServer("neml2-ls", "v0.2.0")

# Inspect-feature state (populated on initialize).
_inspect_caps: dict[str, Any] = {
    "json_supported": False,
    "version": None,
    "binary": None,
    "reason": "inspect capability probe has not run yet",
}
_code_lens_enabled: bool = True

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


@server.feature(lsp.INITIALIZE)
def _on_initialize(ls: LanguageServer, params: lsp.InitializeParams) -> None:
    """Read initialization options sent by the client (codeLensEnabled flag)."""
    global _code_lens_enabled
    opts = params.initialization_options
    if isinstance(opts, dict):
        _code_lens_enabled = bool(opts.get("codeLensEnabled", True))


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

    # Probe the inspect feature and inform the client of the result so it knows
    # whether to enable the palette command's active path. The CodeLens handler
    # also reads this state to decide whether to emit lenses at all.
    global _inspect_caps
    _inspect_caps = probe_inspect()
    ls.protocol.notify(
        "neml2/capabilities",
        {
            "inspectJsonSupported": _inspect_caps["json_supported"],
            "neml2InspectVersion": _inspect_caps["version"],
            "inspectReason": _inspect_caps.get("reason"),
        },
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


# ---------------------------------------------------------------------------
# inspect feature: CodeLens + custom requests
# ---------------------------------------------------------------------------


def _iter_models(lines: list[str]) -> list:
    """Return ParsedBlock objects that live directly under [Models]."""
    return [b for b in parse_all_blocks(lines) if b.context.section == "Models"]


def _uri_to_path(uri: str) -> Path | None:
    """Convert a file:// URI to a filesystem path. Returns None for non-file URIs."""
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    return Path(unquote(parsed.path))


@server.feature(lsp.TEXT_DOCUMENT_CODE_LENS)
def code_lens(
    ls: LanguageServer, params: lsp.CodeLensParams
) -> list[lsp.CodeLens]:
    """Emit a '🔬 Inspect model' lens above each [model] under [Models]."""
    if not _code_lens_enabled or not _inspect_caps.get("json_supported"):
        return []
    lines = _get_lines(ls, params.text_document.uri)
    lenses: list[lsp.CodeLens] = []
    for blk in _iter_models(lines):
        lenses.append(
            lsp.CodeLens(
                range=lsp.Range(
                    start=lsp.Position(line=blk.start_line, character=0),
                    end=lsp.Position(line=blk.start_line, character=0),
                ),
                command=lsp.Command(
                    title="$(search) Inspect model",
                    command="neml2.inspectModel",
                    arguments=[params.text_document.uri, blk.context.block_name],
                ),
            )
        )
    return lenses


@server.feature("neml2/listModels")
def list_models(ls: LanguageServer, params: dict) -> list[dict]:
    """Return [{name, line}, ...] for every top-level model in the document."""
    uri = params.get("uri") if isinstance(params, dict) else getattr(params, "uri", None)
    if not uri:
        return []
    lines = _get_lines(ls, uri)
    return [{"name": blk.context.block_name, "line": blk.start_line} for blk in _iter_models(lines)]


@server.feature("neml2/inspect")
def inspect(ls: LanguageServer, params: dict) -> dict:
    """Run neml2-inspect on the *current buffer* (saved or not) and return the JSON."""
    def _get(key: str) -> Any:
        return params.get(key) if isinstance(params, dict) else getattr(params, key, None)

    uri: str = _get("uri") or ""
    model: str = _get("model") or ""
    if not uri or not model:
        return {"retcode": 1, "error": "neml2/inspect: missing uri or model"}

    if not _inspect_caps.get("json_supported"):
        reason = _inspect_caps.get("reason") or "neml2-inspect --json is not available."
        version = _inspect_caps.get("version") or "unknown"
        return {
            "retcode": 1,
            "error": f"{reason}\n\nDetected neml2 version: {version}",
        }

    doc = ls.workspace.get_text_document(uri)
    source = doc.source

    # Pick the temp file's directory: same dir as the original so relative
    # !include references resolve, with a fallback to the system temp dir for
    # untitled buffers or read-only directories.
    src_path = _uri_to_path(uri)
    work_dir: Path
    fallback_warning: str | None = None
    if src_path is not None:
        work_dir = src_path.parent
    else:
        work_dir = Path(tempfile.gettempdir())

    tmp_file: tempfile._TemporaryFileWrapper | None = None
    try:
        try:
            tmp_file = tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".i",
                prefix=".neml2-inspect-",
                dir=str(work_dir),
                delete=False,
            )
        except (PermissionError, OSError):
            work_dir = Path(tempfile.gettempdir())
            fallback_warning = (
                f"warning: could not write a temp file next to the source; "
                f"falling back to {work_dir} (relative !include paths may fail to resolve)\n\n"
            )
            tmp_file = tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".i",
                prefix=".neml2-inspect-",
                dir=str(work_dir),
                delete=False,
            )

        tmp_file.write(source)
        tmp_file.close()

        result = run_inspect(Path(tmp_file.name), model)
        payload = result.to_dict()
        if fallback_warning and payload.get("error"):
            payload["error"] = fallback_warning + payload["error"]
        return payload
    finally:
        if tmp_file is not None:
            try:
                os.unlink(tmp_file.name)
            except OSError:
                pass


@server.feature("neml2/configChanged")
def config_changed(ls: LanguageServer, params: dict) -> None:
    """Handle a client-pushed configuration update for inspect-related settings."""
    global _code_lens_enabled
    if not isinstance(params, dict):
        return
    if "codeLensEnabled" in params:
        new_value = bool(params["codeLensEnabled"])
        if new_value != _code_lens_enabled:
            _code_lens_enabled = new_value
            try:
                ls.workspace_code_lens_refresh(None)
            except Exception:
                # Refresh is best-effort; VS Code will eventually re-request.
                pass
