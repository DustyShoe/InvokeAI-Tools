"""Cancellation regression checks, using only temporary data."""
from contextlib import closing
import importlib.util
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

REPO = Path(__file__).resolve().parents[1]


def load(relative, name):
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class CancellationTests(unittest.TestCase):
    def test_conversion_rolls_back_mid_batch(self):
        for script in ('Convert_Board_to_Assets_v1.1.py', 'Convert_Assets_to_Board_v1.1.py'):
            with self.subTest(script=script), tempfile.TemporaryDirectory() as directory:
                marker = Path(directory) / 'stop'
                module = load('Database tools/' + script, 'conversion')
                with closing(sqlite3.connect(':memory:')) as conn:
                    conn.executescript("CREATE TABLE images(image_name TEXT, image_category TEXT, image_origin TEXT); INSERT INTO images VALUES('a','original','original'); INSERT INTO images VALUES('b','original','original');")
                    calls = 0
                    def requested():
                        nonlocal calls
                        calls += 1
                        return calls == 3  # After the first UPDATE, before the second.
                    function = getattr(module, 'mark_images_as_assets', None) or module.mark_images_as_general
                    with patch.object(module, 'stop_requested', requested):
                        with self.assertRaises(SystemExit) as result:
                            function(conn, ['a', 'b'], False, False)
                    self.assertEqual(result.exception.code, 130)
                    self.assertEqual(conn.execute('SELECT image_category FROM images').fetchall(), [('original',), ('original',)])
                    self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_restore_rolls_back_completed_image_update(self):
        module = load('Database tools/Restore_Images_DB_v3.1.py', 'restore')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / 'test.db'
            images = root / 'images'
            images.mkdir()
            for name in ('a.png', 'b.png'):
                Image.new('RGB', (4, 4)).save(images / name)
            with closing(sqlite3.connect(db)) as conn:
                conn.executescript("CREATE TABLE images(image_name TEXT, metadata TEXT); CREATE TABLE board_images(board_id TEXT, image_name TEXT); INSERT INTO images VALUES('a.png',NULL); INSERT INTO images VALUES('b.png',NULL);")
            marker = root / 'stop'
            def metadata(path):
                marker.touch()
                return '{"test":true}', False, 4, 4
            args = ['restore', '--db-path', str(db), '--outputs-path', str(images), '--no-backup']
            with patch.dict(os.environ, INVOKEAI_TOOLS_STOP_FILE=str(marker)), patch.object(sys, 'argv', args), patch.object(module, 'read_image_metadata', metadata):
                with self.assertRaises(SystemExit) as result:
                    module.main()
            self.assertEqual(result.exception.code, 130)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute('SELECT metadata FROM images').fetchall(), [(None,), (None,)])
                self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_png_stops_after_complete_atomic_write(self):
        module = load('png_recompress_level9_v1.0.py', 'png_tool')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('a.png', 'b.png'):
                Image.new('RGB', (64, 64), 'red').save(root / name, compress_level=0)
            originals = {p: p.read_bytes() for p in root.glob('*.png')}
            marker = root / 'stop'
            processed = []
            original_process = module.process_file
            def process(path, **kwargs):
                result = original_process(path, **kwargs)
                processed.append(path)
                marker.touch()
                return result
            with patch.dict(os.environ, INVOKEAI_TOOLS_STOP_FILE=str(marker)), patch.object(sys, 'argv', ['png', str(root)]), patch.object(module, 'process_file', process):
                with self.assertRaises(SystemExit) as result:
                    module.main()
            self.assertEqual(result.exception.code, 130)
            self.assertEqual(len(processed), 1)
            self.assertNotEqual(processed[0].read_bytes(), originals[processed[0]])
            for path, original in originals.items():
                with Image.open(path) as image:
                    image.load()
                    self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
                if path not in processed:
                    self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()

