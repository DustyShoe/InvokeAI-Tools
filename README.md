# InvokeAI Database Tools

For easier use, try the [GUI version of this toolset — InvokeAI-Tools-GUI](https://github.com/DustyShoe/InvokeAI-Tools-GUI).

Utilities for recovering InvokeAI image records and metadata, and converting board images between gallery images and user assets. The database scripts are in `Database tools/`.

> <h3 align="center" style="color:red; font-weight:bold;"> ⚠️ WARNING: ⚠️</h3>
> <h3 align="center" style="color:red; font-weight:bold;">
> Always back up your <code>invokeai.db</code> before running any scripts!
>   
> These tools modify the database directly and changes cannot be undone. </h3>

## Current scripts

| Script | Purpose |
| --- | --- |
| `Database tools/Restore_Images_DB_v3.1.py` | Register missing image files and restore missing metadata in existing records. |
| `Database tools/Convert_Board_to_Assets_v1.1.py` | Convert images on a named board to user assets (`user` / `external`). |
| `Database tools/Convert_Assets_to_Board_v1.1.py` | Convert images on a named board to gallery images (`general` / `internal`). |

## Recommended setup: InvokeAI Launcher Dev Console

Copy the scripts you need from this repository's `Database tools/` directory to either:

- The InvokeAI root folder, where `invokeai.yaml` is located.
- A `Tools` folder you create inside the InvokeAI root.

Open the InvokeAI Launcher's **Dev Console**. It automatically activates the InvokeAI virtual environment and changes the working directory to the InvokeAI root. You can run the script commands below immediately; no manual activation or `cd` command is needed. Stop InvokeAI before applying database changes.

Keep the console in the InvokeAI root for both layouts. `./` in the commands below means the current folder: `./script.py` runs a script in the root, and `./Tools/script.py` runs one stored in `Tools`. Do not change into `Tools`.

For example, preview a conversion with the script in the root:

```powershell
python ./Convert_Board_to_Assets_v1.1.py --board-name "Some_Board" --dry-run
```

Or with the script in `Tools`:

```powershell
python ./Tools/Convert_Board_to_Assets_v1.1.py --board-name "Some_Board" --dry-run
```

The examples below use the `Tools` layout. If you copied the scripts directly to the InvokeAI root, omit `Tools/` from each script path.

## Working directory and configuration

Run the database scripts with the **InvokeAI root as the current working directory**. This is the directory containing `invokeai.yaml`, normally alongside `databases/` and `outputs/`. Configuration is read from this working directory, even when the scripts are stored in `Tools/`.

The scripts read these settings from `invokeai.yaml`:

```yaml
db_dir: databases
outdir: outputs
```

`outputs_dir` is also accepted in place of `outdir`. The legacy `InvokeAI.Paths` configuration is supported as well. Relative paths are resolved against the current working directory; absolute paths are used directly.

- The database is `<db_dir>/invokeai.db`.
- The image directory is `<outdir>/images`, so `outdir` should point to `outputs`, not `outputs/images`.
- Missing settings default to `databases/invokeai.db` and `outputs/images`.
- Both paths must already exist.

The conversion scripts also use these defaults when `invokeai.yaml` is absent. They accept no manual path arguments: `--db`, `--db-path`, and `--outputs-path` are not supported.

The current restore script requires `invokeai.yaml` for automatic path discovery. Without a usable configuration, supply both `--db-path` and `--outputs-path` (see below).

## Restore image records and metadata

**Script:** `Database tools/Restore_Images_DB_v3.1.py`

The script scans PNG, JPEG, and WebP files recursively, excluding every `thumbnails` directory.

- Files missing from the database are registered with `image_category = 'general'`, `image_origin = 'internal'`, and `is_intermediate = 0`.
- New images with embedded metadata are added to `Import-yy-mm-dd`.
- New images without recoverable metadata are registered with SQL `NULL` metadata and added to `No metadata-yy-mm-dd`.
- Existing boards with these names are reused.
- Existing image records with missing metadata are updated when metadata can be recovered from the file; their board assignment is preserved.
- Existing records that already have metadata are skipped.
- Existing records without metadata are also skipped if no supported metadata can be recovered from the file.

Before writing changes, the script copies the database to a timestamped file in `backup/` beside `invokeai.db`. A dry run does not write changes, create boards, or create a backup.

### Examples (PowerShell)

In the Dev Console, keep the working directory set to your InvokeAI root. These examples assume the scripts were copied to `Tools/`.

```powershell
# Preview using invokeai.yaml.
python ./Tools/Restore_Images_DB_v3.1.py --dry-run

# Apply changes using invokeai.yaml.
python ./Tools/Restore_Images_DB_v3.1.py

# Scan only files directly inside the images directory.
python ./Tools/Restore_Images_DB_v3.1.py --no-recurse

# Preview at most 100 image files.
python ./Tools/Restore_Images_DB_v3.1.py --dry-run --limit 100
```

The restore script additionally supports explicit paths. Replace the placeholder paths below with your actual locations. `--db-path` points to the database file. `--outputs-path` points directly to any folder containing images, including an external folder with its own structure. It does not need to contain an `outputs/images` subdirectory; for example, it could be `D:/Images/InvokeAI outputs/`. Quote paths containing spaces:

```powershell
python ./Tools/Restore_Images_DB_v3.1.py --db-path "X:/path/to/databases/invokeai.db" --outputs-path "X:/path/to/external image folder" --dry-run
```

| Option | Behavior |
| --- | --- |
| `--dry-run` | Report planned changes without modifying the database. |
| `--no-recurse` | Scan only the top-level images directory; recursion is enabled by default. |
| `--limit N` | Process at most N files; 0 means no limit. |
| `--no-backup` | Skip the automatic database backup. |
| `--db-path PATH` | Override the path to `invokeai.db`. |
| `--outputs-path PATH` | Scan this image folder directly; any folder structure is accepted. |

### Understanding skipped records

- `Skipped (already in DB with metadata)`: the image already has metadata in the database, so its file is not read for metadata recovery.
- `Skipped (already in DB; no metadata found in file)`: the record exists, but the database metadata is missing and the file provides no recoverable metadata.

New files without metadata are imported, rather than counted as skipped.

## Convert board images to assets or gallery images

Both conversion scripts select a board by name, without case sensitivity. They process only board images whose filenames are found under the configured image directory, including subdirectories and excluding `thumbnails` directories.

| Script | `image_category` | `image_origin` |
| --- | --- | --- |
| `Convert_Board_to_Assets_v1.1.py` | `user` | `external` |
| `Convert_Assets_to_Board_v1.1.py` | `general` | `internal` |

Board membership, metadata, `is_intermediate`, timestamps, other database fields, and image files are preserved. These tools reclassify existing records; they do not import files or move images to a different board.

Records whose files are missing are left unchanged and reported as `Skipped (file not found in images directory)`. With `--verbose`, the missing filenames are listed as well.

### Examples (PowerShell)

Run from the InvokeAI root in the Dev Console, with the scripts copied to `Tools/` (or omit `Tools/` if they are in the root). Database and image paths are selected automatically from YAML or the standard directories.

```powershell
# Preview converting a board to user assets.
python ./Tools/Convert_Board_to_Assets_v1.1.py --board-name "Some_Board" --dry-run --verbose

# Apply the conversion to user assets.
python ./Tools/Convert_Board_to_Assets_v1.1.py --board-name "Some_Board"

# Preview converting a board to regular gallery images.
python ./Tools/Convert_Assets_to_Board_v1.1.py --board-name "Some_Board" --dry-run --verbose

# Apply the conversion to gallery images.
python ./Tools/Convert_Assets_to_Board_v1.1.py --board-name "Some_Board"
```

| Option | Behavior |
| --- | --- |
| `--board-name NAME` | Required: name of the board to process. |
| `--dry-run` | List the images that would be updated without writing changes. |
| `--verbose` | Print additional details, including missing filenames. |

## Using an external terminal

The following setup is only for an external terminal. The Dev Console performs activation and the directory change automatically.

Use an environment with Python 3.9 or newer, Pillow, and PyYAML. The restore script uses Pillow and PyYAML; the conversion scripts use PyYAML when a configuration file is present.

The recommended method is the InvokeAI Launcher's Dev Console, as described above. If you prefer another terminal, activate your InvokeAI virtual environment manually and change the working directory to the InvokeAI root.

### Windows PowerShell

Replace `X:/path/to/InvokeAI` with your actual InvokeAI root (the folder containing `invokeai.yaml`). The commands below change to that folder and activate the virtual environment, assuming it is stored in `.venv` there:

```powershell
cd "X:/path/to/InvokeAI"
. ./.venv/Scripts/Activate.ps1
```

For CMD, use `.venv\Scripts\activate.bat` instead. If your environment is stored elsewhere, use its actual activation path.

### Linux / macOS

```bash
cd /path/to/InvokeAI
source /path/to/venv/bin/activate
python ./Tools/Restore_Images_DB_v3.1.py --dry-run
python ./Tools/Convert_Board_to_Assets_v1.1.py --board-name "Some_Board" --dry-run
python ./Tools/Convert_Assets_to_Board_v1.1.py --board-name "Some_Board" --dry-run
```

Use `python ./Tools/Restore_Images_DB_v3.1.py --help` (or the corresponding script name) to see its supported options. Omit `Tools/` for scripts placed directly in the root.
