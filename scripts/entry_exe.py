"""Shared frozen entry point: no arguments opens GUI; commands use CLI."""
import sys

from wasdify.cli import main as cli_main


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    return cli_main(argv or ["gui"])


if __name__ == "__main__":
    raise SystemExit(main())
