import atexit
import json
import subprocess
import threading
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import Any

from ._neml2_bin import find_neml2_cli


NEML2_MIN_VERSION = "3.0.2"
NMHIT_MIN_VERSION = "0.2.2"


def _neml2_ok() -> bool:
    try:
        v = _pkg_version("neml2")
        min_t = tuple(int(x) for x in NEML2_MIN_VERSION.split(".")[:3])
        return tuple(int(x) for x in v.split(".")[:3]) >= min_t
    except PackageNotFoundError:
        return False


class SyntaxClient:
    """Long-lived wrapper around `neml2-syntax --server`.

    ``load`` is a list of user-extension paths forwarded as ``--load`` flags
    to the subprocess so that user-defined types appear alongside built-in
    ones. Because the catalog is fully snapshotted at process start, changing
    the list at runtime requires restarting the subprocess — handled by
    :func:`get_client` reading the cached list and rebuilding on mismatch.
    """

    def __init__(self, load: list[str] | None = None) -> None:
        cmd = find_neml2_cli("neml2-syntax")
        argv = [*cmd, "--server"]
        for path in load or []:
            argv.extend(["--load", path])
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self.load = tuple(load or ())
        self._lock = threading.Lock()
        self._next_id = 1
        atexit.register(self.close)

    def _request(self, method: str, **params: Any) -> Any:
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            payload = {"id": req_id, "method": method, **params}
            assert self._proc.stdin is not None
            assert self._proc.stdout is not None
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
            line = self._proc.stdout.readline()
        resp = json.loads(line)
        if "error" in resp:
            raise RuntimeError(f"neml2-syntax error: {resp['error']}")
        return resp["result"]

    def list_sections(self) -> list[str]:
        return self._request("list_sections")

    def list_types(self, section: str = "") -> list[dict]:
        return self._request("list_types", section=section)

    def get_options(self, type_name: str) -> dict | None:
        return self._request("get_options", type=type_name)

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()


_client: SyntaxClient | None = None


def get_client(load: list[str] | None = None) -> SyntaxClient | None:
    """Return the long-lived ``SyntaxClient``, creating or rebuilding as needed.

    The ``neml2-syntax --server`` subprocess snapshots its registry at start,
    so a change to the ``--load`` list mid-session requires tearing down the
    old subprocess and spawning a new one. Comparing the cached ``load``
    against the requested one keeps this transparent to callers.
    """
    if not _neml2_ok():
        return None
    global _client
    desired = tuple(load or ())
    if _client is not None and _client.load != desired:
        _client.close()
        _client = None
    if _client is None:
        _client = SyntaxClient(list(desired))
    return _client
