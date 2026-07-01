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
import re
import subprocess
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any

from ._neml2_bin import find_neml2_cli


_DEFAULT_INSPECT_TIMEOUT_S = 15.0

#: ``neml2-inspect --json`` -- the only mode this extension uses -- exists in
#: every neml2 >= this version, so we gate the feature on the distribution
#: version rather than shelling out to ``--help``.
_MIN_JSON_VERSION = "3.0.2"


def _version_tuple(v: str) -> tuple[int, ...]:
    """Parse the leading numeric ``major.minor.patch`` of a version string.

    Tolerant of a leading ``v`` and of non-numeric suffixes on any segment
    (e.g. ``"v3.0.6"``, ``"3.0.6.dev0+g1234"``) so metadata quirks don't break
    the comparison.
    """
    stripped = v.strip().lstrip("vV")
    parts: list[int] = []
    for seg in stripped.split(".")[:3]:
        m = re.match(r"\d+", seg)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts)


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
    """Capability probe based on the installed ``neml2`` distribution version.

    Returns ``{"json_supported": bool, "version": str | None, "binary": str | None,
    "reason": str | None}`` where ``reason`` is populated when the probe fails or
    JSON support is missing.

    We gate ``--json`` support on the ``neml2`` version read from
    ``importlib.metadata`` rather than shelling out to ``neml2-inspect --help``.
    The old ``--help`` probe forced a full ``neml2`` + ``torch`` import just to
    grep for a flag; on a cold cache that routinely exceeded the short timeout
    and spuriously disabled the whole Inspect feature for the session. The
    version is authoritative here (v3 is Python-native and ``--json`` has been
    present since |min|), so no subprocess is needed.
    """
    try:
        binary = " ".join(find_neml2_cli("neml2-inspect"))
    except (RuntimeError, ImportError) as exc:
        return {
            "json_supported": False,
            "version": None,
            "binary": None,
            "reason": f"could not locate neml2-inspect: {exc}",
        }

    try:
        version = _pkg_version("neml2")
    except PackageNotFoundError:
        return {
            "json_supported": False,
            "version": None,
            "binary": binary,
            "reason": "the 'neml2' package is not installed in the active interpreter.",
        }

    json_supported = _version_tuple(version) >= _version_tuple(_MIN_JSON_VERSION)
    reason = None
    if not json_supported:
        reason = (
            f"neml2 {version} does not support `neml2-inspect --json`. "
            f"Upgrade to neml2 >= {_MIN_JSON_VERSION} to enable the Inspect feature."
        )

    return {
        "json_supported": json_supported,
        "version": version,
        "binary": binary,
        "reason": reason,
    }
