#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from job_hunter_agent.db import initialize_database, open_database, resolve_db_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    db_path = resolve_db_path(args.db_path)
    with open_database(db_path) as conn:
        initialize_database(conn)
    print(f"Initialized database schema at {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
