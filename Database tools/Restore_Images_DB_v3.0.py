# pylint: disable=line-too-long
# pylint: disable=broad-exception-caught
"""Rescan InvokeAI output images and repair the image database.

This standalone maintenance script scans ``outputs/images`` recursively by
default, ignores every ``thumbnails`` directory, and reads metadata embedded in
PNG, JPEG, and WebP files. It restores recoverable metadata for existing image
records and registers image files that are missing from the InvokeAI database.

New images with embedded metadata are added to a daily ``Import-yy-mm-dd``
board. New images without embedded metadata are registered with NULL metadata
and added to a daily ``No metadata-yy-mm-dd`` board. Existing boards with these
names are reused. Use ``--no-recurse`` to scan only the top-level images
directory, ``--dry-run`` to report changes without writing them, and
``--no-backup`` to skip the automatic database backup.

InvokeAI should be stopped before running this script because it writes
directly to the SQLite database.

Examples (PowerShell)::

    # Use the paths configured in invokeai.yaml (run from the InvokeAI root).
    python ./rescan_image_metadata_v1.0.py

    # Override both the database and output-image paths.
    python ./rescan_image_metadata_v1.0.py --db-path "D:/InvokeAI/databases/invokeai.db" --outputs-path "E:/InvokeAI/outputs/images"

    # Preview changes without modifying the database or creating boards.
    python ./rescan_image_metadata_v1.0.py --db-path "D:/InvokeAI/databases/invokeai.db" --outputs-path "E:/InvokeAI/outputs/images" --dry-run

    # Scan only files directly inside outputs/images, without subdirectories.
    python ./rescan_image_metadata_v1.0.py --no-recurse
"""

import argparse
import datetime
import json
import locale
import os
import re
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Optional, Tuple

import PIL.Image
import yaml


class ConfigMapper:
    """Configuration loader."""

    YAML_FILENAME = "invokeai.yaml"
    DATABASE_FILENAME = "invokeai.db"

    DEFAULT_OUTDIR = "outputs"
    DEFAULT_DB_DIR = "databases"

    def __init__(self) -> None:
        self.database_path: Optional[Path] = None
        self.database_backup_dir: Optional[Path] = None
        self.outputs_path: Optional[Path] = None

    def load(self, root: Path) -> bool:
        """Read paths from yaml config and validate."""
        yaml_path = root / self.YAML_FILENAME
        if not yaml_path.exists():
            print(f"Unable to find {self.YAML_FILENAME} at {yaml_path}!")
            return False

        db_dir, outdir = self._load_paths_from_yaml_file(yaml_path)
        if db_dir is None:
            db_dir = self.DEFAULT_DB_DIR
            print(f"{self.YAML_FILENAME} missing db_dir, defaulting to {db_dir}")
        if outdir is None:
            outdir = self.DEFAULT_OUTDIR
            print(f"{self.YAML_FILENAME} missing outdir, defaulting to {outdir}")

        if Path(db_dir).is_absolute():
            self.database_path = Path(db_dir) / self.DATABASE_FILENAME
        else:
            self.database_path = root / db_dir / self.DATABASE_FILENAME

        if Path(outdir).is_absolute():
            self.outputs_path = Path(outdir) / "images"
        else:
            self.outputs_path = root / outdir / "images"

        self.database_backup_dir = self.database_path.parent / "backup"

        db_exists = self.database_path.exists()
        outdir_exists = self.outputs_path.exists()
        print(
            f"Found {self.YAML_FILENAME} at {yaml_path}:\n"
            f"  Database: {self.database_path} - {'Exists' if db_exists else 'Not Found'}\n"
            f"  Outputs : {self.outputs_path} - {'Exists' if outdir_exists else 'Not Found'}"
        )

        if not db_exists or not outdir_exists:
            print("One or more paths do not exist. Please fix invokeai.yaml or pass overrides.")
            return False

        return True

    def _load_paths_from_yaml_file(self, yaml_path: Path) -> tuple[Optional[str], Optional[str]]:
        """Load an InvokeAI yaml file and get the database and outputs paths."""
        try:
            with open(yaml_path, "rt", encoding=locale.getpreferredencoding()) as file:
                yamlinfo = yaml.safe_load(file)
                db_dir = yamlinfo.get("InvokeAI", {}).get("Paths", {}).get("db_dir", None)
                outdir = yamlinfo.get("InvokeAI", {}).get("Paths", {}).get("outdir", None)
                if outdir is None:
                    outdir = yamlinfo.get("InvokeAI", {}).get("Paths", {}).get("outputs_dir", None)
                if db_dir is None:
                    db_dir = yamlinfo.get("db_dir", None)
                if outdir is None:
                    outdir = yamlinfo.get("outdir", None)
                if outdir is None:
                    outdir = yamlinfo.get("outputs_dir", None)
                return db_dir, outdir
        except Exception:
            print(f"Failed to load paths from yaml file! {yaml_path}!")
            return None, None


class InvokeAIMetadata:
    """DTO for core Invoke AI generation properties parsed from metadata."""

    def __init__(self) -> None:
        self.generation_mode = None
        self.steps = None
        self.cfg_scale = None
        self.model_name = None
        self.scheduler = None
        self.seed = None
        self.width = None
        self.height = None
        self.rand_device = None
        self.strength = None
        self.init_image = None
        self.positive_prompt = None
        self.negative_prompt = None
        self.imported_app_version = None

    def to_json(self) -> str:
        """Convert the active instance to json format."""
        prop_dict = {}
        prop_dict["generation_mode"] = self.generation_mode
        if self.positive_prompt or self.negative_prompt:
            prop_dict["positive_prompt"] = "" if self.positive_prompt is None else self.positive_prompt
            prop_dict["negative_prompt"] = "" if self.negative_prompt is None else self.negative_prompt
        prop_dict["width"] = self.width
        prop_dict["height"] = self.height
        if self.seed:
            prop_dict["seed"] = self.seed
        prop_dict["rand_device"] = self.rand_device
        prop_dict["cfg_scale"] = self.cfg_scale
        prop_dict["steps"] = self.steps
        prop_dict["scheduler"] = self.scheduler
        prop_dict["clip_skip"] = 0
        prop_dict["model"] = {}
        prop_dict["model"]["model_name"] = self.model_name
        prop_dict["model"]["base_model"] = None
        prop_dict["controlnets"] = []
        prop_dict["loras"] = []
        prop_dict["vae"] = None
        prop_dict["strength"] = self.strength
        prop_dict["init_image"] = self.init_image
        prop_dict["positive_style_prompt"] = None
        prop_dict["negative_style_prompt"] = None
        prop_dict["refiner_model"] = None
        prop_dict["refiner_cfg_scale"] = None
        prop_dict["refiner_steps"] = None
        prop_dict["refiner_scheduler"] = None
        prop_dict["refiner_aesthetic_store"] = None
        prop_dict["refiner_start"] = None
        prop_dict["imported_app_version"] = self.imported_app_version

        return json.dumps(prop_dict)


class InvokeAIMetadataParser:
    """Parses strings with json data to find Invoke AI core metadata properties."""

    def parse_meta_tag_dream(self, dream_string: str) -> InvokeAIMetadata:
        """Take as input a legacy dream prompt string from pre-1.15."""
        props = InvokeAIMetadata()

        props.imported_app_version = "pre1.15"
        seed_match = re.search(r"-S\s*(\d+)", dream_string)
        if seed_match is not None:
            try:
                props.seed = int(seed_match[1])
            except ValueError:
                props.seed = None
            raw_prompt = re.sub(r"(-S\s*\d+)", "", dream_string)
        else:
            raw_prompt = dream_string

        pos_prompt, neg_prompt = self.split_prompt(raw_prompt)

        props.positive_prompt = pos_prompt
        props.negative_prompt = neg_prompt

        return props

    def parse_meta_tag_sd_metadata(self, tag_value: dict) -> InvokeAIMetadata:
        """Parse sd-metadata JSON from InvokeAI 1.15 through 2.3.5 post-2."""
        props = InvokeAIMetadata()

        props.imported_app_version = tag_value.get("app_version")
        props.model_name = tag_value.get("model_weights")
        img_node = tag_value.get("image")
        if img_node is not None:
            props.generation_mode = img_node.get("type")
            props.width = img_node.get("width")
            props.height = img_node.get("height")
            props.seed = img_node.get("seed")
            props.rand_device = "cuda"
            props.cfg_scale = img_node.get("cfg_scale")
            props.steps = img_node.get("steps")
            props.scheduler = self.map_scheduler(img_node.get("sampler"))
            props.strength = img_node.get("strength")
            if props.strength is None:
                props.strength = img_node.get("strength_steps")
            props.init_image = img_node.get("init_image_path")
            if props.init_image is None:
                props.init_image = img_node.get("init_img")
            if props.init_image is not None:
                props.init_image = os.path.basename(props.init_image)
            raw_prompt = img_node.get("prompt")
            if isinstance(raw_prompt, list):
                raw_prompt = raw_prompt[0].get("prompt")

            props.positive_prompt, props.negative_prompt = self.split_prompt(raw_prompt)

        return props

    def parse_meta_tag_invokeai(self, tag_value: dict) -> InvokeAIMetadata:
        """Parse invokeai JSON from 3.0.0 beta 1 through 5."""
        props = InvokeAIMetadata()

        props.imported_app_version = "3.0.0 or later"
        props.generation_mode = tag_value.get("type")
        if props.generation_mode is not None:
            props.generation_mode = props.generation_mode.replace("t2l", "txt2img").replace("l2l", "img2img")

        props.width = tag_value.get("width")
        props.height = tag_value.get("height")
        props.seed = tag_value.get("seed")
        props.cfg_scale = tag_value.get("cfg_scale")
        props.steps = tag_value.get("steps")
        props.scheduler = tag_value.get("scheduler")
        props.strength = tag_value.get("strength")
        props.positive_prompt = tag_value.get("positive_conditioning")
        props.negative_prompt = tag_value.get("negative_conditioning")

        return props

    def map_scheduler(self, old_scheduler: Optional[str]) -> Optional[str]:
        """Convert legacy sampler names to matching 3.0 schedulers."""
        if old_scheduler is None:
            return None
        scheduler_map = {
            "ddim": "ddim",
            "plms": "pnmd",
            "k_lms": "lms",
            "k_dpm_2": "kdpm_2",
            "k_dpm_2_a": "kdpm_2_a",
            "dpmpp_2": "dpmpp_2s",
            "k_dpmpp_2": "dpmpp_2m",
            "k_dpmpp_2_a": None,
            "k_euler": "euler",
            "k_euler_a": "euler_a",
            "k_heun": "heun",
        }
        return scheduler_map.get(old_scheduler)

    def split_prompt(self, raw_prompt: Optional[str]) -> tuple[str, str]:
        """Split unified prompt strings into positive and negative prompts."""
        if raw_prompt is None:
            return "", ""
        raw_prompt_search = raw_prompt.replace("\r", "").replace("\n", "")
        matches = re.findall(r"\[(.+?)\]", raw_prompt_search)
        if len(matches) > 0:
            negative_prompt = ""
            if len(matches) == 1:
                negative_prompt = matches[0].strip().strip(",")
            else:
                for match in matches:
                    negative_prompt += f"({match.strip().strip(',')})"
            positive_prompt = re.sub(r"(\[.+?\])", "", raw_prompt_search).strip()
        else:
            positive_prompt = raw_prompt_search.strip()
            negative_prompt = ""

        return positive_prompt, negative_prompt


def _coerce_info_value(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return str(value)


def read_image_metadata(image_path: Path) -> Tuple[Optional[str], bool, int, int]:
    """Read metadata and dimensions from an image file."""
    parser = InvokeAIMetadataParser()

    with PIL.Image.open(image_path) as img:
        img.load()
        width, height = img.size
        info = img.info

    has_workflow = "invokeai_workflow" in info or "invokeai_graph" in info

    latest_json_string = _coerce_info_value(info.get("invokeai_metadata"))
    if latest_json_string:
        return latest_json_string, has_workflow, width, height

    converted_field: Optional[InvokeAIMetadata] = None

    sd_metadata = _coerce_info_value(info.get("sd-metadata"))
    if sd_metadata:
        try:
            converted_field = parser.parse_meta_tag_sd_metadata(json.loads(sd_metadata))
        except json.JSONDecodeError:
            converted_field = None
    else:
        invokeai_metadata = _coerce_info_value(info.get("invokeai"))
        if invokeai_metadata:
            try:
                converted_field = parser.parse_meta_tag_invokeai(json.loads(invokeai_metadata))
            except json.JSONDecodeError:
                converted_field = None
        else:
            dream_metadata = _coerce_info_value(info.get("dream")) or _coerce_info_value(info.get("Dream"))
            if dream_metadata:
                converted_field = parser.parse_meta_tag_dream(dream_metadata)

    if converted_field is None:
        return None, has_workflow, width, height

    if converted_field.width is None:
        converted_field.width = width
    if converted_field.height is None:
        converted_field.height = height

    return converted_field.to_json(), has_workflow, width, height


def is_metadata_missing(value: Optional[str]) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() in ("", "null", "None"):
        return True
    return False


def iter_image_files(outputs_path: Path, recurse: bool) -> list[Path]:
    pattern = "**/*" if recurse else "*"
    image_paths = []
    for path in outputs_path.glob(pattern):
        if not path.is_file():
            continue
        if any(part.lower() == "thumbnails" for part in path.relative_to(outputs_path).parts):
            continue
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        image_paths.append(path)
    return image_paths


def backup_db(database_path: Path, backup_dir: Path) -> None:
    """Take a backup of the database."""
    if not backup_dir.exists():
        print(f"Database backup directory {backup_dir} does not exist -> creating...", end="")
        backup_dir.mkdir(parents=True, exist_ok=True)
        print("Done!")
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"backup-{timestamp}-invokeai.db"
    print(f"Making DB backup at {backup_path}...", end="")
    shutil.copy2(database_path, backup_path)
    print("Done!")


def get_table_columns(cursor: sqlite3.Cursor, table_name: str) -> set[str]:
    """Return the column names for a SQLite table."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {column[1] for column in cursor.fetchall()}


def get_or_create_board(cursor: sqlite3.Cursor, board_name: str) -> Tuple[str, bool]:
    """Get a system-owned board by name, creating it when necessary."""
    board_columns = get_table_columns(cursor, "boards")
    if not board_columns:
        raise RuntimeError("The database does not contain a boards table")

    if "user_id" in board_columns:
        cursor.execute(
            "SELECT board_id FROM boards WHERE board_name = ? AND COALESCE(user_id, 'system') = 'system' "
            "ORDER BY created_at LIMIT 1",
            (board_name,),
        )
    else:
        cursor.execute(
            "SELECT board_id FROM boards WHERE board_name = ? ORDER BY created_at LIMIT 1",
            (board_name,),
        )

    existing_board = cursor.fetchone()
    if existing_board is not None:
        return existing_board[0], False

    board_id = str(uuid.uuid4())
    columns = ["board_id", "board_name"]
    values = [board_id, board_name]
    if "user_id" in board_columns:
        columns.append("user_id")
        values.append("system")

    placeholders = ",".join("?" for _ in columns)
    cursor.execute(
        f"INSERT INTO boards ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    return board_id, True


def main() -> int:
    parser = argparse.ArgumentParser(description="Rescan outputs and restore missing metadata in the DB.")
    parser.add_argument("--db-path", type=Path, help="Override path to invokeai.db")
    parser.add_argument("--outputs-path", type=Path, help="Override path to outputs/images directory")
    parser.add_argument(
        "--recurse",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Recurse into subfolders under outputs/images (default: enabled; use --no-recurse to disable)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Scan and report without updating the DB")
    parser.add_argument("--no-backup", action="store_true", help="Skip DB backup before updating")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of files to process (0 = no limit)")
    args = parser.parse_args()

    config = ConfigMapper()
    root = Path.cwd()
    config_loaded = config.load(root)

    database_path = args.db_path or (config.database_path if config_loaded else None)
    outputs_path = args.outputs_path or (config.outputs_path if config_loaded else None)
    if database_path is None or outputs_path is None:
        print("Unable to determine database or outputs paths.")
        return 1

    if not config_loaded and (args.db_path is None or args.outputs_path is None):
        return 1

    backup_dir = config.database_backup_dir
    if args.db_path is not None or backup_dir is None:
        backup_dir = database_path.parent / "backup"

    if not database_path.exists():
        print(f"Database not found: {database_path}")
        return 1
    if not outputs_path.exists():
        print(f"Outputs/images not found: {outputs_path}")
        return 1

    print(f"Using database: {database_path}")
    print(f"Using outputs : {outputs_path}")

    if not args.dry_run and not args.no_backup:
        backup_db(database_path, backup_dir)

    connection = sqlite3.connect(str(database_path))
    cursor = connection.cursor()

    columns = get_table_columns(cursor, "images")
    has_workflow_column = "has_workflow" in columns

    if not get_table_columns(cursor, "board_images"):
        print("Database does not contain the required board_images table.")
        connection.close()
        return 1

    cursor.execute("SELECT image_name, metadata FROM images")
    existing_metadata = {row[0]: row[1] for row in cursor.fetchall()}

    image_paths = iter_image_files(outputs_path, args.recurse)
    if args.limit > 0:
        image_paths = image_paths[: args.limit]

    stats = {
        "processed": 0,
        "inserted": 0,
        "updated": 0,
        "skipped_has_metadata": 0,
        "skipped_no_metadata": 0,
        "errors": 0,
        "added_to_import_board": 0,
        "added_to_no_metadata_board": 0,
    }
    date_suffix = datetime.datetime.now().strftime("%y-%m-%d")
    import_board_name = f"Import-{date_suffix}"
    no_metadata_board_name = f"No metadata-{date_suffix}"
    board_ids: dict[str, str] = {}
    created_boards: list[str] = []

    for image_path in image_paths:
        image_name = image_path.name
        stats["processed"] += 1

        try:
            metadata_value = existing_metadata.get(image_name)
            if metadata_value is not None and not is_metadata_missing(metadata_value):
                stats["skipped_has_metadata"] += 1
                continue

            metadata_json, has_workflow, width, height = read_image_metadata(image_path)
            # Existing rows without recoverable metadata need no update. New image files
            # are still registered (with NULL metadata) and added to the import board.
            if metadata_json is None and image_name in existing_metadata:
                stats["skipped_no_metadata"] += 1
                continue

            if image_name in existing_metadata:
                stats["updated"] += 1
                if not args.dry_run:
                    if has_workflow_column:
                        cursor.execute(
                            "UPDATE images SET metadata = ?, has_workflow = ? WHERE image_name = ?",
                            (metadata_json, bool(has_workflow), image_name),
                        )
                    else:
                        cursor.execute(
                            "UPDATE images SET metadata = ? WHERE image_name = ?",
                            (metadata_json, image_name),
                        )
            else:
                stats["inserted"] += 1
                created_at = datetime.datetime.fromtimestamp(
                    os.path.getmtime(image_path),
                    datetime.timezone.utc,
                )
                created_at_str = created_at.strftime("%Y-%m-%d %H:%M:%S")
                columns = [
                    "image_name",
                    "image_origin",
                    "image_category",
                    "width",
                    "height",
                    "metadata",
                    "is_intermediate",
                    "created_at",
                    "updated_at",
                ]
                values = [
                    image_name,
                    "internal",
                    "general",
                    width,
                    height,
                    metadata_json,
                    False,
                    created_at_str,
                    created_at_str,
                ]
                if has_workflow_column:
                    columns.append("has_workflow")
                    values.append(bool(has_workflow))

                placeholders = ",".join("?" for _ in columns)
                query = f"INSERT INTO images ({', '.join(columns)}) VALUES ({placeholders})"
                target_board_name = no_metadata_board_name if metadata_json is None else import_board_name

                if not args.dry_run:
                    target_board_id = board_ids.get(target_board_name)
                    if target_board_id is None:
                        target_board_id, board_created = get_or_create_board(cursor, target_board_name)
                        board_ids[target_board_name] = target_board_id
                        if board_created:
                            created_boards.append(target_board_name)
                    cursor.execute(query, values)
                    cursor.execute(
                        "INSERT INTO board_images (board_id, image_name) VALUES (?, ?)",
                        (target_board_id, image_name),
                    )
                if metadata_json is None:
                    stats["added_to_no_metadata_board"] += 1
                else:
                    stats["added_to_import_board"] += 1
                existing_metadata[image_name] = metadata_json

        except Exception as exc:
            stats["errors"] += 1
            print(f"Error processing {image_path}: {exc}")

    if not args.dry_run:
        connection.commit()
    connection.close()

    print("\nRescan complete:")
    print(f"  Processed            : {stats['processed']}")
    print(f"  Inserted             : {stats['inserted']}")
    print(f"  Metadata updated     : {stats['updated']}")
    print(f"  Skipped (has metadata): {stats['skipped_has_metadata']}")
    print(f"  Skipped (no metadata): {stats['skipped_no_metadata']}")
    print(f"  Errors               : {stats['errors']}")
    print(f"  Added to {import_board_name}: {stats['added_to_import_board']}")
    print(f"  Added to {no_metadata_board_name}: {stats['added_to_no_metadata_board']}")
    for board_name in created_boards:
        print(f"  Created board         : {board_name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
