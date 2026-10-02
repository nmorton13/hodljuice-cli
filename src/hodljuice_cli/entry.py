"""Console entry point. Standard library only: `hj ctl` must start fast."""

import sys

VERSION = "0.1.0"


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "ctl":
        from hodljuice_cli import ctl

        sys.exit(ctl.main(sys.argv[2:]))
    if cmd == "playerd":
        from hodljuice_cli import playerd

        sys.exit(playerd.main(sys.argv[2:]))
    if cmd == "fortune":
        # Runs at shell startup: keep it away from Typer and never raise.
        from hodljuice_cli import fortune

        sys.exit(fortune.main(sys.argv[2:]))
    if cmd in ("--version", "-V"):
        print(f"hj {VERSION}")
        return
    from hodljuice_cli.cli import app

    app()


if __name__ == "__main__":
    main()
