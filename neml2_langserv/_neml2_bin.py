"""Shared helper for locating binaries shipped with the `neml2` PyPI package."""
from __future__ import annotations

from pathlib import Path


def find_neml2_binary(name: str) -> Path:
    """Locate a binary bundled in the installed `neml2` package's `bin/` directory.

    Raises RuntimeError listing the searched paths if no such file exists.
    """
    import neml2  # imported lazily so non-inspect features keep working when neml2 is missing

    for pkg_dir in neml2.__path__:
        candidate = Path(pkg_dir) / "bin" / name
        if candidate.exists():
            return candidate
    searched = [str(Path(p) / "bin" / name) for p in neml2.__path__]
    raise RuntimeError(f"{name} not found; searched: {searched}")
