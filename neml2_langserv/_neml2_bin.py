"""Locate neml2 CLI tools shipped as console scripts of the installed `neml2` package.

neml2 v2 shipped C++ binaries under `<site-packages>/neml2/bin/<name>`. neml2 v3 is
Python-native and exposes the same tools as Python console scripts (see
`[project.scripts]` in neml2's `pyproject.toml`), backed by modules under
`neml2.cli.*`. The console scripts land in the env's `bin/` rather than inside the
package, so the old direct-path lookup no longer finds them.

The resolver below uses the installed package's `console_scripts` entry-point
metadata to find the backing module, then returns a `python -m <module>` command.
Going through the current interpreter rather than PATH guarantees we hit the same
`neml2` install this process imports, regardless of how the language server was
launched.
"""
from __future__ import annotations

import sys
from importlib.metadata import entry_points


def find_neml2_cli(name: str) -> list[str]:
    """Resolve a ``neml2-*`` console script to a runnable subprocess command.

    Returns a command list (e.g. ``[sys.executable, "-m", "neml2.cli.inspect"]``)
    suitable for :func:`subprocess.run` / :class:`subprocess.Popen`. Raises
    ``RuntimeError`` when the named console script is not exposed by an installed
    ``neml2`` distribution.
    """
    matches = [
        ep
        for ep in entry_points(group="console_scripts", name=name)
        if ep.dist is not None and ep.dist.name == "neml2"
    ]
    if not matches:
        raise RuntimeError(
            f"{name!r} is not exposed by the installed `neml2` package's console_scripts. "
            f"Confirm `pip show neml2` reports a version >= 3.0.2."
        )
    # entry-point value is "module:attr" — drop the attr for `python -m`.
    module = matches[0].value.split(":")[0]
    return [sys.executable, "-m", module]
