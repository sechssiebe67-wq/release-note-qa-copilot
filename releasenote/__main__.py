"""Entry point for ``python -m releasenote``."""

from .server import main

if __name__ == "__main__":
    raise SystemExit(main())
