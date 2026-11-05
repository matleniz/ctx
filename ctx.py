#!/usr/bin/env python3
"""ctx launcher: the entry point that installed hooks and the ~/.local/bin/ctx link call.

The code lives in the package src/ctx (standard library only). This file finds src/ next to its
own real path, so the clone can move, and calls ctx.cli.main().
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"

if not (SRC / "ctx" / "__init__.py").is_file():
    print(f"ctx: package missing at {SRC / 'ctx'}, reinstall the clone", file=sys.stderr)
    sys.exit(0 if sys.argv[1:2] == ["hook"] else 1)  # a hook must always exit 0
sys.path.insert(0, str(SRC))

from ctx.cli import main  # noqa: E402

sys.exit(main())
