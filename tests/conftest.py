import os
import socket

import pytest

# MLflow 3.17 refuses the file:// store unless opted in; the tracking tests use one under
# tmp_path so nothing leaves the machine. The hint is a one-line stderr advert for an agent skill.
os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def requires_service(host: str, port: int, name: str):
    return pytest.mark.skipif(not _reachable(host, port), reason=f"{name} not reachable")
