#!/usr/bin/env python3
"""
InvokeAI images reindex tool.

- Scans outputs/images/ for PNG files
- For each PNG missing in `images` table:
    * inserts a new row
    * image_category = 'general'
    * image_origin   = 'internal'    
- Puts all newly inserted images into a dedicated board:
    "Import dd-mm-yy HH:MM"
- Optionally generates thumbnails for these images.

No heuristics, no guessing assets/intermediate. Everything restored is
treated as a normal internal image (image_origin="internal", image_category="general").
User intervention is required for further classification, sorting, or cleanup in the UI.
"""
import argparse
import sqlite3
import json
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional

from PIL import Image

THUMB_SIZE = 256  # thumbnail long side, pixels


def build_arg_parser():
    """
    Build and return the argument parser for the InvokeAI images reindex tool.

    Returns:
        argparse.ArgumentParser: Configured argument parser.
    """
    parser = argparse.ArgumentParser(
        description="Rebuild InvokeAI images index (simple import)"
    )
    parser.add_argument(
        "--db",
        required=True,
        help="Path to invokeai.db (SQLite file)",
    )
    parser.add_argument(
        "--outputs",
        required=True,
        help="Path to InvokeAI outputs directory (root of outputs/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not modify DB or filesystem, only print actions",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output",
    )
    parser.add_argument(
        "--gen-thumbs",
        action="store_true",
        help="Generate thumbnails for newly inserted images if missing",
    )
    return parser

def parse_args():
    """
    Parse command-line arguments for the InvokeAI images reindex tool.

    Returns:
        argparse.Namespace: Parsed arguments including db path, outputs directory,
        dry-run flag, verbose flag, and gen-thumbs flag.
    """
    parser = build_arg_parser()
    return parser.parse_args()


def load_png_metadata(path: Path):
    """
    Open PNG with Pillow and extract InvokeAI metadata chunks.

    Returns:
        Tuple[
            Image.Image,         # img: Pillow Image object
            Optional[str],       # meta_raw: raw string from "invokeai_metadata" (or None)
            Optional[dict],      # meta_json: parsed JSON from meta_raw (or None)
            Optional[str],       # graph_raw: raw string from "invokeai_graph" (or None)
            bool                 # has_graph: True if "invokeai_graph" chunk is present
        ]
    """
    img = Image.open(path)
    info = img.info

    meta_raw = info.get("invokeai_metadata")
    meta_json = None
    if meta_raw:
        try:
            meta_json = json.loads(meta_raw)
        except json.JSONDecodeError:
            meta_json = None
    graph_raw = info.get("invokeai_graph")
    has_graph = graph_raw is not None

    return img, meta_raw, meta_json, graph_raw, has_graph


def detect_created_at(meta_json: Optional[dict], path: Path) -> str:
    """
    Determine creation time.

    Priority:
      1. Timestamp in metadata (if present and valid)
      2. File modification time (mtime)

    Returns:
      string formatted as '%Y-%m-%d %H:%M:%S.%f' (SQLite friendly)
    """
    dt = None

    if meta_json:
        for key in ("created", "created_at", "timestamp", "time"):
            val = meta_json.get(key)
            if isinstance(val, str):
                for fmt in (
                    "%Y-%m-%dT%H:%M:%S.%fZ",
                    "%Y-%m-%dT%H:%M:%S%z",
                    "%Y-%m-%d %H:%M:%S",
                ):
                    try:
                        dt = datetime.strptime(val, fmt)
                        break
                    except Exception:
                        continue
            if dt:
                break

    if dt is None:
        dt = datetime.fromtimestamp(path.stat().st_mtime)

    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


def image_exists(conn: sqlite3.Connection, image_name: str) -> bool:
    """
    Check if an image with this name already exists in the DB.
    """
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM images WHERE image_name = ?", (image_name,))
    return cur.fetchone() is not None


def insert_image(
    conn: sqlite3.Connection,
    image_name: str,
    img: Image.Image,
    meta_raw: Optional[str],
    has_graph: bool,
    created_at: str,
    dry_run: bool,
    verbose: bool,
):
    """
    Insert new image row.

    All restored images are treated as:
      image_category = 'general'
      image_origin   = 'internal'
      is_intermediate = 0
    """
    width, height = img.size
    image_category = "general"
    image_origin = "internal"
    has_workflow = 1 if has_graph else 0
    is_intermediate = 0

    if dry_run:
        print(
            f"[DRY][INSERT] {image_name} "
            f"cat={image_category}, origin={image_origin}, "
            f"intermediate={is_intermediate}, has_workflow={has_workflow}"
        )
        return

    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO images (
            image_name,
            image_origin,
            image_category,
            width,
            height,
            metadata,
            is_intermediate,
            created_at,
            updated_at,
            has_workflow
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            image_name,
            image_origin,
            image_category,
            width,
            height,
            meta_raw,
            is_intermediate,
            created_at,
            created_at,
            has_workflow,
        ),
    )
    conn.commit()

    if verbose:
        print(f"[INSERT] {image_name}")


def ensure_import_board(
    conn: sqlite3.Connection,
    dry_run: bool,
    verbose: bool,
) -> str:
    """
    Ensure there is a 'Recovered DD-MM-YY' board.
    board_id is always a UUID v4.
    If board_name already exists, reuse it.
    """
    from uuid import uuid4

    today = datetime.now().strftime("%d-%m-%y")
    board_name = f"Recovered {today}"

    cur = conn.cursor()
    # Check if board with this name already exists
    cur.execute("SELECT board_id FROM boards WHERE board_name = ?", (board_name,))
    row = cur.fetchone()
    if row:
        board_id = row[0]
        if verbose:
            print(f"[BOARD] Reusing board '{board_name}' (id={board_id})")
        return board_id

    # Create a new board with UUID
    board_id = str(uuid4())

    if dry_run:
        if verbose:
            print(f"[DRY][BOARD] Would create board '{board_name}' (id={board_id})")
        return board_id

    cur.execute(
        "INSERT INTO boards (board_id, board_name) VALUES (?, ?)",
        (board_id, board_name),
    )
    conn.commit()

    if verbose:
        print(f"[BOARD] Created board '{board_name}' (id={board_id})")

    return board_id

def add_to_board(
    conn: sqlite3.Connection,
    board_id: str,
    image_name: str,
    dry_run: bool,
    verbose: bool,
):
    """
    Attach image to the import board.
    """
    if dry_run:
        if verbose:
            print(f"[DRY][BOARD] Would link {image_name} -> {board_id}")
        return

    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO board_images (board_id, image_name)
        VALUES (?, ?)
        """,
        (board_id, image_name),
    )
    conn.commit()

    if verbose:
        print(f"[BOARD] Linked {image_name} -> {board_id}")


def get_thumbnail_path(outputs_root: Path, image_path: Path) -> Path:
    """
    Compute thumbnail path.

    Example:
      outputs/images/foo.png -> outputs/images/thumbnails/foo.webp

    Raises:
        ValueError: if image_path is not under outputs_root.
    """
    try:
        rel = image_path.relative_to(outputs_root)
    except ValueError:
        raise ValueError(f"image_path '{image_path}' is not under outputs_root '{outputs_root}'")

    parts = list(rel.parts)

    if parts and parts[0] == "images":
        parts.insert(1, "thumbnails")

            # force .webp extension for thumbnails
    stem = Path(parts[-1]).stem + ".webp"
    parts[-1] = stem
    
    return outputs_root / Path(*parts)

def ensure_thumbnail(
    outputs_root: Path,
    image_path: Path,
    dry_run: bool,
    verbose: bool,
    img: Optional[Image.Image] = None,
):
    """
    Ensure thumbnail file exists for the given image.

    Thumbnails are not indexed in the DB; they are just files on disk.
    Optionally accepts an already opened Pillow Image object to avoid redundant I/O.
    """
    thumb_path = get_thumbnail_path(outputs_root, image_path)

    if thumb_path.exists():
        return

    if dry_run:
        if verbose:
            print(f"[DRY][THUMB] Would create {thumb_path}")
        return

    thumb_path.parent.mkdir(parents=True, exist_ok=True)

    close_img = False
    if img is None:
        img = Image.open(image_path)
        close_img = True

    try:
        img = img.convert("RGB")
        w, h = img.size
        if w >= h:
            new_w = THUMB_SIZE
            new_h = int(h * THUMB_SIZE / max(w, 1))
        else:
            new_h = THUMB_SIZE
            new_w = int(w * THUMB_SIZE / max(h, 1))
        img = img.resize((new_w, new_h), Image.LANCZOS)
        img.save(thumb_path, format="WEBP", quality=90)
    finally:
        if close_img:
            img.close()

    if verbose:
        print(f"[THUMB] Created {thumb_path}")


def main():
    args = parse_args()

    db_path = Path(args.db)
    outputs_root = Path(args.outputs)
    images_root = outputs_root / "images"
    
    with sqlite3.connect(db_path) as conn:
        # Create one import board for this run
        import_board_id = ensure_import_board(conn, args.dry_run, args.verbose)
        # Only search for PNGs in images_root and its subdirectories, excluding 'thumbnails'
        png_files = sorted(
            p for p in images_root.glob("**/*.png")
            if "thumbnails" not in p.parts
        )
        for path in png_files:
            # Use relative path from images_root as image_name to avoid collisions
            image_name = str(path.relative_to(images_root))

            if image_exists(conn, image_name):
                if args.verbose:
                    print(f"[SKIP] {image_name} already in DB")
                continue

            try:
                img, meta_raw, meta_json, graph_raw, has_graph = load_png_metadata(path)
            except Exception as e:
                print(f"[ERR] Failed to read {path}: {e}")
                continue

            created_at = detect_created_at(meta_json, path)

            if args.verbose:
                print(f"[FILE] {path} -> name={image_name}")

            try:
                # Insert image row
                insert_image(
                    conn,
                    image_name,
                    img,
                    meta_raw,
                    has_graph,
                    created_at,
                    args.dry_run,
                    args.verbose,
                )

                # Link to import board
                add_to_board(
                    conn,
                    import_board_id,
                    image_name,
                    args.dry_run,
                    args.verbose,
                )
            except Exception as e:
                print(f"[ERR] Failed to insert {image_name}: {e}")
                img.close()
                continue

            # Generate thumbnail if requested
            if args.gen_thumbs:
                ensure_thumbnail(outputs_root, path, args.dry_run, args.verbose, img)

    if not args.dry_run or args.verbose:
        print("Done.")


if __name__ == "__main__":
    main()
    print("Done.")