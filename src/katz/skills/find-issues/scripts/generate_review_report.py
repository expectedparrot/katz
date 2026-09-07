#!/usr/bin/env python3
"""Compatibility entry point for the packaged report renderer."""
from katz.reporting import *  # noqa: F401,F403

def main():
    from katz.reporting import main as report_main
    return report_main()


if __name__ == "__main__":
    main()
