"""The suite itself must never be able to spend money."""
import os
import socket
import subprocess
import sys

import pytest

from hayclips.config import PAID_OPT_IN_ENV, load_settings


def test_paid_opt_in_and_key_removed():
    assert PAID_OPT_IN_ENV not in os.environ and "HARMAR_API_KEY" not in os.environ
    assert load_settings().allow_paid_harmar is False
    assert load_settings().harmar_base_url.startswith("http://127.0.0.1")


def test_non_loopback_network_blocked():
    with pytest.raises(RuntimeError, match="offline-only"):
        socket.create_connection(("api.harmar.ai", 443), timeout=1)


def test_suite_refuses_to_start_with_opt_in(tmp_path):
    env = dict(os.environ, **{PAID_OPT_IN_ENV: "1"})
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/test_guards.py::test_paid_opt_in_and_key_removed"],
                       env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 3 and "refusing to run" in (r.stdout + r.stderr)
