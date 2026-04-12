from __future__ import annotations

import argparse
from pathlib import Path

from nanobot.harness.service import HarnessService


def migrate_and_sync(workspace_root: Path) -> None:
    if not workspace_root.exists() or not workspace_root.is_dir():
        raise NotADirectoryError(f"workspace path is not an existing directory: {workspace_root}")
    if not (workspace_root / "scripts").is_dir():
        raise NotADirectoryError(f"workspace path is missing workspace markers: {workspace_root}")
    service = HarnessService.for_workspace(workspace_root)
    service.sync_projections()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nanobot.harness.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    migrate_parser = subparsers.add_parser("migrate-and-sync")
    migrate_parser.add_argument("workspace_root")

    args = parser.parse_args(argv)
    if args.command == "migrate-and-sync":
        migrate_and_sync(Path(args.workspace_root).expanduser().resolve())
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
