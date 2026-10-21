"""Fake archives only; never downloads or reads deployed ONNX model files."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / 'models/wholebody/download_models.py'
spec = importlib.util.spec_from_file_location('wholebody_download_models', SCRIPT)
download = importlib.util.module_from_spec(spec)
spec.loader.exec_module(download)


def archive(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as zipped:
        for name, content in entries:
            zipped.writestr(name, content)
    return buffer.getvalue()


class ModelDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.content = {'dwpose-l': b'fake-dwpose-model', 'rtmw-l': b'fake-rtmw-model'}
        self.manifest = {name: dict(url=f'https://example.invalid/{name}.zip',
                                   sha256=hashlib.sha256(content).hexdigest(), bytes=len(content),
                                   input=[1, 3, 256, 192]) for name, content in self.content.items()}
        self.manifest_path = self.root / 'manifest.json'
        self.manifest_bytes = json.dumps(self.manifest, indent=2).encode()
        self.manifest_path.write_bytes(self.manifest_bytes)

    def run_main(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return download.main(args, root=self.root)

    def assert_unchanged_manifest(self):
        self.assertEqual(self.manifest_bytes, self.manifest_path.read_bytes())
        self.assertFalse(list(self.root.glob('.*-*')))

    def test_selected_model_downloaded_atomically_without_rewriting_manifest(self):
        model = 'rtmw-l'
        old = self.root / f'{model}.onnx'
        old.write_bytes(b'old-existing-model')
        real_replace = download.os.replace
        def replace(src, dst):
            self.assertEqual(old.read_bytes(), b'old-existing-model')
            self.assertEqual(Path(src).read_bytes(), self.content[model])
            self.assertEqual(Path(dst), old)
            real_replace(src, dst)
        body = archive([('../../not-extracted/model.onnx', self.content[model])])
        with patch.object(download.urllib.request, 'urlopen', return_value=io.BytesIO(body)) as request, \
                patch.object(download.os, 'replace', side_effect=replace) as replacement:
            self.assertEqual(self.run_main('--model', model), 0)
        request.assert_called_once_with(self.manifest[model]['url'], timeout=60)
        replacement.assert_called_once()
        self.assertEqual(old.read_bytes(), self.content[model])
        self.assertFalse((self.root/'dwpose-l.onnx').exists())
        self.assert_unchanged_manifest()

    def test_default_handles_both_and_existing_valid_files_never_download(self):
        for name, content in self.content.items():
            (self.root/f'{name}.onnx').write_bytes(content)
        with patch.object(download.urllib.request, 'urlopen', side_effect=AssertionError('no network')):
            self.assertEqual(self.run_main(), 0)
            self.assertEqual(self.run_main('--check'), 0)
        self.assert_unchanged_manifest()

    def test_check_is_readonly_for_missing_size_and_hash_errors(self):
        dest = self.root/'rtmw-l.onnx'
        for content in (None, b'short', b'x'*len(self.content['rtmw-l'])):
            with self.subTest(content=content):
                if content is not None:
                    dest.write_bytes(content)
                with patch.object(download.urllib.request, 'urlopen', side_effect=AssertionError('no network')):
                    with self.assertRaises(SystemExit) as failed:
                        self.run_main('--model', 'rtmw-l', '--check')
                self.assertEqual(failed.exception.code, 1)
                self.assertEqual(dest.read_bytes() if dest.exists() else None, content)
                self.assert_unchanged_manifest()

    def test_bad_archives_never_overwrite_existing_model(self):
        expected = self.content['rtmw-l']
        cases = [b'not a zip', archive([]), archive([('a.onnx', expected), ('b.onnx', expected)]),
                 archive([('a.onnx', b'bad-size')]), archive([('a.onnx', b'x'*len(expected))])]
        dest = self.root/'rtmw-l.onnx'
        for body in cases:
            with self.subTest(body_length=len(body)):
                dest.write_bytes(b'old-model')
                with patch.object(download.urllib.request, 'urlopen', return_value=io.BytesIO(body)):
                    with self.assertRaises(SystemExit):
                        self.run_main('--model', 'rtmw-l')
                self.assertEqual(dest.read_bytes(), b'old-model')
                self.assert_unchanged_manifest()

    def test_interrupted_download_cleans_temporary_files_and_preserves_old(self):
        class BrokenResponse(io.BytesIO):
            def read(self, size=-1):
                if self.tell():
                    raise OSError('connection lost')
                return super().read(5)
        dest = self.root/'rtmw-l.onnx'
        dest.write_bytes(b'old-model')
        with patch.object(download.urllib.request, 'urlopen', return_value=BrokenResponse(b'partial-archive')):
            with self.assertRaises(SystemExit):
                self.run_main('--model', 'rtmw-l')
        self.assertEqual(dest.read_bytes(), b'old-model')
        self.assert_unchanged_manifest()

    def test_failed_atomic_replace_preserves_old(self):
        dest = self.root/'rtmw-l.onnx'
        dest.write_bytes(b'old-model')
        body = archive([('a.onnx', self.content['rtmw-l'])])
        with patch.object(download.urllib.request, 'urlopen', return_value=io.BytesIO(body)), \
                patch.object(download.os, 'replace', side_effect=PermissionError('read only')):
            with self.assertRaises(SystemExit):
                self.run_main('--model', 'rtmw-l')
        self.assertEqual(dest.read_bytes(), b'old-model')
        self.assert_unchanged_manifest()

    def test_malformed_manifest_rejected_before_network(self):
        for key, value in (('sha256', 'invalid'), ('bytes', True), ('bytes', 0), ('url', 'file:///tmp/model.zip')):
            with self.subTest(key=key, value=value):
                manifest = json.loads(self.manifest_bytes)
                manifest['rtmw-l'][key] = value
                self.manifest_path.write_text(json.dumps(manifest))
                before = self.manifest_path.read_bytes()
                with patch.object(download.urllib.request, 'urlopen', side_effect=AssertionError('no network')):
                    with self.assertRaises(SystemExit):
                        self.run_main('--model', 'rtmw-l')
                self.assertEqual(self.manifest_path.read_bytes(), before)
                self.assertFalse((self.root/'rtmw-l.onnx').exists())


if __name__ == '__main__':
    unittest.main()
