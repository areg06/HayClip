"""Start the local operator app: .venv/bin/python -m hayclips.web [--port 8765]

Binds 127.0.0.1 only. Binding any other address exposes creator media to the network and needs
--i-understand-this-exposes-creator-media. The worker runs separately (it executes the queued jobs).
"""
import argparse
import sys

import uvicorn

from .app import create_app
from .security import allowed_hosts_for

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hayclips.web")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--i-understand-this-exposes-creator-media", dest="expose", action="store_true")
    a = ap.parse_args(argv)
    if a.host not in LOOPBACK and not a.expose:
        print("refusing to bind a non-loopback address; this app has no login and serves creator media", file=sys.stderr)
        return 2
    hosts = allowed_hosts_for(a.port) | ({f"{a.host}:{a.port}"} if a.expose else set())
    app = create_app(port=a.port, allowed_hosts=hosts)
    print(f"HayClips operator app on http://127.0.0.1:{a.port}/  (start the worker separately)")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning", server_header=False, proxy_headers=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
