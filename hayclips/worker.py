"""Entry point: python -m hayclips.worker --pools io,cpu,paid"""
import sys

from .jobs.worker import main

if __name__ == "__main__":
    sys.exit(main())
