"""Command dispatcher for subtitle-toolkit."""

from __future__ import annotations

import argparse
import sys

from .caption import main as caption_main
from .removal import main as removal_main


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="subtitle-toolkit")
    parser.add_argument("command", choices=("caption", "remove"))
    if not arguments or arguments == ["-h"] or arguments == ["--help"]:
        parser.parse_args(arguments)
        return 0
    command, remainder = arguments[0], arguments[1:]
    if command not in ("caption", "remove"):
        parser.error(f"argument command: invalid choice: {command!r} (choose from 'caption', 'remove')")
    return caption_main(remainder) if command == "caption" else removal_main(remainder)


if __name__ == "__main__":
    raise SystemExit(main())
