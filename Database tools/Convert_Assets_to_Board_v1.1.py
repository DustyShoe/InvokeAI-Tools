#!/usr/bin/env python3
"""Mark all images on a named InvokeAI board as regular internal images.

For every image linked to the board, set image_category = 'general' and
image_origin = 'internal'. Board membership, image files, metadata, and other
database fields are preserved. The board name is matched case-insensitively.

Run from the InvokeAI root. Database and image paths are read automatically
from invokeai.yaml: db_dir and outdir (or outputs_dir). Both top-level keys and
legacy InvokeAI.Paths keys are supported. Relative paths are resolved against
the current working directory. If YAML or a key is absent, the defaults are
databases/invokeai.db and outputs/images. No manual path arguments are needed.
Only board images whose files exist under the images directory are processed;
subdirectories are included and thumbnails directories are ignored. Missing
files are reported and their database records are left unchanged.

Stop InvokeAI and back up the database before applying changes. Use --dry-run
to preview the affected images without modifying the database.

Examples (PowerShell)::

    # Run from the InvokeAI root, using the database configured in invokeai.yaml.
    python ./Convert_Assets_to_Board_v1.1.py --board-name "Some_Board"

    # Preview changes using the configured database.
    python ./Convert_Assets_to_Board_v1.1.py --board-name "Some_Board" --dry-run --verbose

If the script is stored outside the current directory, replace ./ above with
its full path; in PowerShell, put paths containing spaces in double quotes.
"""

import argparse
import os
import sqlite3
from pathlib import Path
from typing import List



def stop_requested() -> bool:
    """Cooperative GUI cancellation; never interrupt a write in progress."""
    marker = os.environ.get("INVOKEAI_TOOLS_STOP_FILE")
    return bool(marker and Path(marker).is_file())


def cancellation_checkpoint(connection=None) -> None:
    if stop_requested():
        if connection is not None:
            connection.rollback()
        print("[STOPPED] Stopped safely. Pending database changes were rolled back.", flush=True)
        raise SystemExit(130)


def resolve_paths() -> tuple[Path, Path]:
    """Read paths from invokeai.yaml in cwd, using defaults for missing keys."""
    root = Path.cwd()
    yaml_path = root / "invokeai.yaml"
    config = {}
    paths = {}
    if yaml_path.is_file():
        try:
            import yaml
        except ImportError as exc:
            raise SystemExit(
                "Reading invokeai.yaml requires PyYAML. "
                "Install it with 'python -m pip install PyYAML'."
            ) from exc
        try:
            with yaml_path.open("r", encoding="utf-8-sig") as file:
                config = yaml.safe_load(file)
            if config is None:
                config = {}
            if not isinstance(config, dict):
                raise ValueError("configuration must be a mapping")
            invokeai = config.get("InvokeAI", {})
            if invokeai is None:
                invokeai = {}
            if not isinstance(invokeai, dict):
                raise ValueError("InvokeAI must be a mapping")
            paths = invokeai.get("Paths", {})
            if paths is None:
                paths = {}
            if not isinstance(paths, dict):
                raise ValueError("InvokeAI.Paths must be a mapping")
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
            raise SystemExit(f"Unable to read {yaml_path}: {exc}") from exc
    else:
        print("invokeai.yaml not found; using databases/invokeai.db and outputs/images.")

    def configured_directory(keys: tuple[str, ...], default: str) -> Path:
        value = next(
            (mapping[key] for mapping in (paths, config) for key in keys
             if mapping.get(key) is not None),
            default,
        )
        if not isinstance(value, str) or not value.strip():
            raise SystemExit(f"Invalid {keys[0]} in {yaml_path}: expected a non-empty path string.")
        directory = Path(value).expanduser()
        return directory if directory.is_absolute() else root / directory

    database_path = configured_directory(("db_dir",), "databases") / "invokeai.db"
    outputs_path = configured_directory(("outdir", "outputs_dir"), "outputs") / "images"
    if not database_path.is_file():
        raise SystemExit(f"Database not found: {database_path}. Run from the InvokeAI root.")
    if not outputs_path.is_dir():
        raise SystemExit(f"Images directory not found: {outputs_path}. Run from the InvokeAI root.")
    return database_path.resolve(), outputs_path.resolve()


def filter_existing_images(image_names: List[str], outputs_path: Path, verbose: bool) -> List[str]:
    """Keep board images present in outputs/images, excluding thumbnails."""
    available = {
        path.name for path in outputs_path.rglob("*")
        if not cancellation_checkpoint() and path.is_file()
        and not any(part.lower() == "thumbnails" for part in path.relative_to(outputs_path).parts)
    }
    existing = [name for name in image_names if name in available]
    missing = [name for name in image_names if name not in available]
    if missing:
        print(f"Skipped (file not found in images directory): {len(missing)}")
        if verbose:
            for name in missing:
                print(f"  - {name}")
    return existing


def build_arg_parser() -> argparse.ArgumentParser:
    """
    Build and return the argument parser for the reclassification tool.
    """
    parser = argparse.ArgumentParser(
        description="Mark all images from a specific board as regular internal images (general/internal)."
    )
    parser.add_argument(
        "--board-name",
        required=True,
        help="Board name whose images should be reclassified as general/internal",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not modify DB, only print what would be changed",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output",
    )
    return parser


def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments for the reclassification tool.
    """
    parser = build_arg_parser()
    return parser.parse_args()


def get_board_id_by_name(conn: sqlite3.Connection, board_name: str) -> str:
    """
    Resolve board_id by board_name (case-insensitive).

    Raises:
        SystemExit if the board cannot be found.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT board_id FROM boards WHERE LOWER(board_name) = LOWER(?)",
        (board_name,),
    )
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"Board with name '{board_name}' not found (case-insensitive).")
    return row[0]


def get_images_for_board(conn: sqlite3.Connection, board_id: str) -> List[str]:
    """
    Return a list of image_name values linked to the given board_id.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT image_name FROM board_images WHERE board_id = ?",
        (board_id,),
    )
    rows = cur.fetchall()
    return [r[0] for r in rows]


def mark_images_as_general(
    conn: sqlite3.Connection,
    image_names: List[str],
    dry_run: bool,
    verbose: bool,
) -> int:
    """
    Mark the given images as regular internal images:

      image_category = 'general'
      image_origin   = 'internal'

    is_intermediate is NOT modified.

    Returns:
        int: number of updated images.
    """
    if not image_names:
        return 0

    if dry_run:
        print("[DRY] Would update the following images as general/internal:")
        for name in image_names:
            cancellation_checkpoint(conn)
            print(f"  - {name}")
        return len(image_names)

    cur = conn.cursor()

    if verbose:
        print(f"[INFO] Updating {len(image_names)} images as general/internal...")

    cancellation_checkpoint(conn)
    cur.executemany(
        """
        UPDATE images
        SET
            image_category = 'general',
            image_origin   = 'internal'
        WHERE image_name = ?
        """,
        ((name,) for name in image_names if not cancellation_checkpoint(conn)),
    )
    cancellation_checkpoint(conn)
    conn.commit()

    if verbose:
        print(f"[OK] Updated {cur.rowcount} rows in images.")

    return cur.rowcount


def main():
    args = parse_args()
    cancellation_checkpoint()

    database_path, outputs_path = resolve_paths()
    print(f"Using database: {database_path}")
    print(f"Using images  : {outputs_path}")
    # mode=rw prevents SQLite from creating a new database if the file disappears.
    conn = sqlite3.connect(database_path.as_uri() + "?mode=rw", uri=True)

    try:
        board_id = get_board_id_by_name(conn, args.board_name)
    except SystemExit:
        conn.close()
        raise

    if args.verbose:
        print(f"[INFO] Using board_name = '{args.board_name}', board_id = {board_id}")

    image_names = get_images_for_board(conn, board_id)
    image_names = filter_existing_images(image_names, outputs_path, args.verbose)

    if not image_names:
        print("No board images with existing files found. Nothing to do.")
        conn.close()
        return

    if args.verbose:
        print(f"[INFO] Found {len(image_names)} images in this board.")

    updated = mark_images_as_general(conn, image_names, args.dry_run, args.verbose)

    if args.dry_run:
        print(f"[DRY] Would update {updated} images.")
    else:
        print(f"Updated {updated} images as general/internal.")

    conn.close()


if __name__ == "__main__":
    main()
    print("Done.")