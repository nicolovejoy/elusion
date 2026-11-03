import socket

import pytest


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def requires_service(host: str, port: int, name: str):
    return pytest.mark.skipif(not _reachable(host, port), reason=f"{name} not reachable")
