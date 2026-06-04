"""Wrapper around the ``neml2-inspect`` CLI tool.

``neml2-inspect --json`` emits a single structured object on stdout matching:

  Success: ``{"retcode": 0, "name", "host", "inputs": [...], "outputs": [...],
              "parameters": [...], "buffers": [...]}``
  Failure: ``{"retcode": <int>, "error": "<message>"}``

The process exit code mirrors ``retcode``.

We always pass ``--json``; an older ``neml2`` that does not understand the flag is
handled at a higher layer by :func:`probe_inspect`, which the server uses to
disable the inspect feature entirely.
"""
from __future__ import annotations

import dataclasses
import json
import subprocess
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any

from ._neml2_bin import find_neml2_cli


_HELP_TIMEOUT_S = 3.0
_DEFAULT_INSPECT_TIMEOUT_S = 15.0


@dataclasses.dataclass
class InspectResult:
    """Structured result of a ``neml2-inspect --json`` invocation.

    ``error`` is ``None`` iff the invocation succeeded (``retcode == 0`` and the
    structured fields are populated). On any failure path the error text is
    placed in ``error`` and the list fields are empty.
    """

    retcode: int = 0
    name: str = ""
    host: str = ""
    inputs: list[dict[str, str]] = dataclasses.field(default_factory=list)
    outputs: list[dict[str, str]] = dataclasses.field(default_factory=list)
    parameters: list[dict[str, str]] = dataclasses.field(default_factory=list)
    buffers: list[dict[str, str]] = dataclasses.field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _from_payload(payload: dict[str, Any]) -> InspectResult:
    """Build an InspectResult from a parsed JSON payload following the schema."""
    rc = int(payload.get("retcode", 1))
    if rc != 0 or "error" in payload:
        return InspectResult(
            retcode=rc if rc != 0 else 1,
            error=str(payload.get("error", "neml2-inspect reported failure with no message")),
        )
    return InspectResult(
        retcode=0,
        name=str(payload.get("name", "")),
        host=str(payload.get("host", "")),
        inputs=list(payload.get("inputs", [])),
        outputs=list(payload.get("outputs", [])),
        parameters=list(payload.get("parameters", [])),
        buffers=list(payload.get("buffers", [])),
    )


def run_inspect(
    input_path: Path,
    model_name: str,
    timeout_s: float = _DEFAULT_INSPECT_TIMEOUT_S,
    load: list[str] | None = None,
) -> InspectResult:
    """Run ``neml2-inspect --json <input_path> <model_name>`` and classify the result.

    ``load`` is forwarded one entry per ``--load`` flag so user-defined types
    referenced by the input file resolve at inspection time.
    """
    try:
        cmd = find_neml2_cli("neml2-inspect")
    except (RuntimeError, ImportError) as exc:  # neml2 missing or console script absent
        return InspectResult(retcode=1, error=f"could not locate neml2-inspect: {exc}")

    argv: list[str] = [*cmd, "--json"]
    for path in load or []:
        argv.extend(["--load", path])
    argv.extend([str(input_path), model_name])
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return InspectResult(
            retcode=1,
            error=f"neml2-inspect timed out after {timeout_s:g}s",
        )
    except (FileNotFoundError, OSError) as exc:
        return InspectResult(retcode=1, error=f"could not run neml2-inspect: {exc}")

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    rc = completed.returncode

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        # Either a very old neml2 (no --json support, argparse error to stderr) or a
        # catastrophic failure (segfault, missing shared lib). Either way, surface
        # both streams plus the exit code so the user can diagnose.
        msg_parts = [f"neml2-inspect produced unparseable output (exit {rc})."]
        if stderr.strip():
            msg_parts.append(f"stderr:\n{stderr.strip()}")
        if stdout.strip():
            msg_parts.append(f"stdout:\n{stdout.strip()}")
        return InspectResult(retcode=rc or 1, error="\n\n".join(msg_parts))

    if not isinstance(payload, dict):
        return InspectResult(
            retcode=rc or 1,
            error=f"neml2-inspect returned non-object JSON (exit {rc}): {stdout!r}",
        )

    return _from_payload(payload)


def probe_inspect() -> dict[str, Any]:
    """One-shot capability probe.

    Returns ``{"json_supported": bool, "version": str | None, "binary": str | None,
    "reason": str | None}`` where ``reason`` is populated when the probe fails or
    JSON support is missing.
    """
    try:
        cmd = find_neml2_cli("neml2-inspect")
    except (RuntimeError, ImportError) as exc:
        return {
            "json_supported": False,
            "version": None,
            "binary": None,
            "reason": f"could not locate neml2-inspect: {exc}",
        }

    help_text = ""
    try:
        completed = subprocess.run(
            [*cmd, "--help"],
            capture_output=True,
            text=True,
            timeout=_HELP_TIMEOUT_S,
        )
        help_text = (completed.stdout or "") + (completed.stderr or "")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return {
            "json_supported": False,
            "version": None,
            "binary": " ".join(cmd),
            "reason": f"could not run `neml2-inspect --help`: {exc}",
        }

    json_supported = "--json" in help_text

    # Distribution metadata is the source of truth for v3 (Python-native; the CLI
    # itself has no --version flag).
    version: str | None = None
    try:
        version = _pkg_version("neml2")
    except PackageNotFoundError:
        pass

    reason = None
    if not json_supported:
        reason = (
            "neml2-inspect in this neml2 build does not support --json output mode. "
            "Upgrade to neml2 >= 3.0.2 to enable the Inspect feature."
        )

    return {
        "json_supported": json_supported,
        "version": version,
        "binary": " ".join(cmd),
        "reason": reason,
    }
