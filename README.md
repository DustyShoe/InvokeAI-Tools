# InvokeAI Database Tools

A collection of utility scripts for working with the InvokeAI SQLite database.  
These tools are designed for recovering lost image entries, rebuilding the image index, and reclassifying images as user assets.

> <h3 style="color:red; font-weight:bold;"> ⚠️ WARNING: ⚠️</h3>
> <h3 style="color:red; font-weight:bold;">
> Always back up your <code>invokeai.db</code> before running any scripts!
>   
> These tools modify the database directly and changes cannot be undone. </h3>

---

## Features

###  Recover missing images from `<OUTPUTS_PATH>/images/`
**Script:** `Restore_Images_DB_v2.1.py`

This script scans the directory you provide via <code>--outputs</code> and compares files to the entries stored in the database.  
Any images that exist on disk but are missing from the database are added back.
The path is defined by the string in your invokeai.yaml.
Script also attempts to recover any metadata image might have.

The script:
- Scans `<OUTPUTS_PATH>/images/`
- Detects all PNG images not present in `images` table of cuirrent DB
- Inserts them with:
  - `image_category = "general"`
  - `image_origin = "internal"`
  - `is_intermediate = 0`
- Creates a board named `Recovered DD-MM-YY`
- Adds all recovered images to that board

---

###  Convert all images from defined board into user assets  
**Script:** `Convert_Board_to_Assets.py`

This tool updates images belonging to a specific board and classifies them as assets.

It:
- Locates a board by name (Not case-sensitive)
- Reads all `image_name` entries from `board_images`
- Updates the corresponding rows in the `images` table:
  - `image_category = "user"`
  - `image_origin = "external"`
- Does *not* modify board membership or any other fields

Useful for reorganizing imports or turning grouped images into asset references.

---

## 1. For users launching InvokeAI via the official Launcher (recommended)


## Running the scripts using the InvokeAI Launcher

If you use the official InvokeAI Launcher, you don’t need to activate the virtual
environment manually. The Launcher provides a dedicated **Dev Console** with the
venv already activated. Just click `>_` button in lower left corner

### Steps

1. Open the **InvokeAI Launcher**.
2. Click `>_` button in lower left corner. Then `Start Dev Console`
   This opens a terminal window with the proper virtual environment activated.
3. In the Dev Console, run:
    ```bash
    python path/to/Restore_Images_DB_v2.1.py --db path/to/invokeai.db --outputs path/to/outputs
    ``` 
    or

    ```bash
    python Convert_Board_to_Assets.py --db path\to\invokeai.db --board-name "Board name" --verbose
    ```

---

## 2. Running the scripts using InvokeAI’s virtual environment in terminal of your choice:

Launch it manually via PowerShell or CMD, you can use the same virtual environment as InvokeAI to run these tools. No separate Python installation needed.

### Windows (CMD or PowerShell)

1. Open a terminal.
2. Navigate to your InvokeAI installation directory:
   ```bash
   cd path/to/InvokeAI
   ```
3. Activate the virtual environment:
   ```bash
   ./venv/Scripts/activate
   ```
4. Run the script:
    
   ```bash
   python path/to/scripts/Restore_Images_DB_v2.1.py --db path/to/invokeai.db --outputs path/to/outputs
   ```
   
   or


   ```bash 
   python path/to/scripts/Convert_Board_to_Assets.py --db path/to/invokeai.db --board-name "Board name to convert" --verbose
   ```

### Linux / macOS

1. Open a terminal.
2. Navigate to your InvokeAI directory:
    
    ```bash
    cd /path/to/InvokeAI
    ```
3. Activate the venv:
   
    ```bash
    source venv/bin/activate
    ```
4. Run the script:

    ```bash
    python /path/to/scripts/Restore_Images_DB_v2.1.py --db /path/to/invokeai.db --outputs /path/to/outputs
    ```

   This uses the same Python environment as InvokeAI, so no extra dependencies are required.


---
