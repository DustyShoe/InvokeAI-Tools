#!/usr/bin/env python3
"""
Mark all images from a specific board as regular internal images in InvokeAI DB.

For all images linked to the given board (by board_name), this script will:
  - set image_category = 'general'
  - set image_origin   = 'internal'

It does NOT touch is_intermediate, created_at, deleted_at, or any other fields.

Usage examples:

  python Convert_Board_to_General.py --db /path/to/invokeai.db --board-name "Assets Board"
  python Convert_Board_to_General.py --db invokeai.db --board-name "Some Board" --dry-run --verbose
"""

import argparse
import sqlite3
from typing import List


def build_arg_parser() -> argparse.ArgumentParser:
    """
    Build and return the argument parser for the reclassification tool.
    """
    parser = argparse.ArgumentParser(
        description="Mark all images from a specific board as regular internal images (general/internal)."
    )
    parser.add_argument(
        "--db",
        required=True,
        help="Path to invokeai.db (SQLite file)",
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
            print(f"  - {name}")
        return len(image_names)

    cur = conn.cursor()

    if verbose:
        print(f"[INFO] Updating {len(image_names)} images as general/internal...")

    cur.executemany(
        """
        UPDATE images
        SET
            image_category = 'general',
            image_origin   = 'internal'
        WHERE image_name = ?
        """,
        [(name,) for name in image_names],
    )
    conn.commit()

    if verbose:
        print(f"[OK] Updated {cur.rowcount} rows in images.")

    return cur.rowcount


def main():
    args = parse_args()

    conn = sqlite3.connect(args.db)

    try:
        board_id = get_board_id_by_name(conn, args.board_name)
    except SystemExit:
        conn.close()
        raise

    if args.verbose:
        print(f"[INFO] Using board_name = '{args.board_name}', board_id = {board_id}")

    image_names = get_images_for_board(conn, board_id)

    if not image_names:
        print("No images found for this board. Nothing to do.")
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