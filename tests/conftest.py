"""Test-suite guards: the suite must be incapable of reaching a real paid provider.

1. If the paid opt-in variable is present in the environment, the whole run aborts.
2. Every test runs with the opt-in and any Harmar key removed, HAYCLIPS_HOME in a temp dir, and the
   Harmar base URL pointed at a closed local port.
3. Outgoing sockets to anything but loopback raise immediately.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from pathlib import Path

import pytest

from hayclips.config import PAID_OPT_IN_ENV

if os.environ.get(PAID_OPT_IN_ENV):
    pytest.exit(f"{PAID_OPT_IN_ENV} is set: refusing to run tests that could spend money", returncode=3)

FIXTURES = Path(__file__).parent / "fixtures"
LOCAL_FIXTURES = FIXTURES / "local"

_real_connect = socket.socket.connect
_real_create_connection = socket.create_connection


def _is_loopback(address) -> bool:
    if isinstance(address, (str, bytes)):          # AF_UNIX
        return True
    host = address[0]
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _guarded_connect(self, address):
    if not _is_loopback(address):
        raise RuntimeError(f"test tried to open a network connection to {address!r}; tests are offline-only")
    return _real_connect(self, address)


def _guarded_create_connection(address, *args, **kwargs):
    if not _is_loopback(address):
        raise RuntimeError(f"test tried to open a network connection to {address!r}; tests are offline-only")
    return _real_create_connection(address, *args, **kwargs)


@pytest.fixture(autouse=True)
def offline_and_unpaid(monkeypatch, tmp_path):
    monkeypatch.delenv(PAID_OPT_IN_ENV, raising=False)
    monkeypatch.delenv("HARMAR_API_KEY", raising=False)
    monkeypatch.setenv("HAYCLIPS_HOME", str(tmp_path / "hayclips-home"))
    monkeypatch.setenv("HAYCLIPS_HARMAR_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket, "create_connection", _guarded_create_connection)
    yield


def pytest_collection_modifyitems(config, items):
    skip_local = pytest.mark.skip(reason="local fixtures absent (tests/fixtures/local, gitignored); run tests/fixtures/make_local_fixtures.py")
    for item in items:
        if "local_fixture" in item.keywords and not LOCAL_FIXTURES.exists():
            item.add_marker(skip_local)
