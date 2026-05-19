"""Wrapper around the ``neml2-inspect`` CLI tool.

The C++ binary (since neml2 2.1.5) supports a ``--json`` mode that emits a single
structured object on stdout matching this schema:

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
from pathlib import Path
from typing import Any

from ._neml2_bin import find_neml2_binary


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
) -> InspectResult:
    """Run ``neml2-inspect --json <input_path> <model_name>`` and classify the result."""
    try:
        binary = find_neml2_binary("neml2-inspect")
    except (RuntimeError, ImportError) as exc:  # neml2 missing or no bin/neml2-inspect
        return InspectResult(retcode=1, error=f"could not locate neml2-inspect: {exc}")

    try:
        completed = subprocess.run(
            [str(binary), "--json", str(input_path), model_name],
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
        binary = find_neml2_binary("neml2-inspect")
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
            [str(binary), "--help"],
            capture_output=True,
            text=True,
            timeout=_HELP_TIMEOUT_S,
        )
        help_text = (completed.stdout or "") + (completed.stderr or "")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return {
            "json_supported": False,
            "version": None,
            "binary": str(binary),
            "reason": f"could not run `neml2-inspect --help`: {exc}",
        }

    json_supported = "--json" in help_text

    version: str | None = None
    # Prefer the Python package's version (cheap, always present when neml2 is importable).
    try:
        import neml2  # type: ignore

        version = getattr(neml2, "__version__", None)
    except ImportError:
        pass
    # Fall back to the binary's own --version output if Python doesn't expose one.
    if version is None:
        try:
            completed = subprocess.run(
                [str(binary), "--version"],
                capture_output=True,
                text=True,
                timeout=_HELP_TIMEOUT_S,
            )
            text = (completed.stdout or completed.stderr or "").strip()
            if text:
                version = text.splitlines()[0]
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

    reason = None
    if not json_supported:
        reason = (
            "neml2-inspect in this neml2 build does not support --json output mode. "
            "Upgrade to neml2 >= 2.1.5 to enable the Inspect feature."
        )

    return {
        "json_supported": json_supported,
        "version": version,
        "binary": str(binary),
        "reason": reason,
    }
