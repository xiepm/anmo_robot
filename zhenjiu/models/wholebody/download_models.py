"""Fetch official ONNX exports using the committed manifest as a trust anchor."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
MODELS = ('dwpose-l', 'rtmw-l')
CHUNK_BYTES = 1024 * 1024


def load_manifest(root, names):
    """Read expected model hashes; never replace them with downloaded hashes."""
    manifest = json.loads((Path(root) / 'manifest.json').read_text())
    if not isinstance(manifest, dict):
        raise ValueError('manifest.json 必须是对象')
    selected = {}
    for name in names:
        spec = manifest.get(name)
        if (name not in MODELS or not isinstance(spec, dict)
                or not isinstance(spec.get('url'), str)
                or not spec['url'].startswith('https://')
                or not isinstance(spec.get('sha256'), str)
                or not re.fullmatch(r'[0-9a-fA-F]{64}', spec['sha256'])
                or type(spec.get('bytes')) is not int or spec['bytes'] <= 0):
            raise ValueError(f'{name}: manifest 缺少有效 HTTPS URL、SHA-256 或字节数')
        selected[name] = spec
    return selected


def verify_file(path, spec):
    path = Path(path)
    if path.stat().st_size != spec['bytes']:
        raise ValueError(f'{path.name}: 字节数不匹配，预期 {spec["bytes"]}')
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(CHUNK_BYTES):
            digest.update(chunk)
    if digest.hexdigest() != spec['sha256'].lower():
        raise ValueError(f'{path.name}: SHA-256 不匹配')


def ensure_model(root, name, spec, *, check_only=False):
    """Publish a verified model atomically; failures leave an old model intact."""
    root = Path(root)
    destination = root / f'{name}.onnx'
    try:
        verify_file(destination, spec)
        return 'verified'
    except (OSError, ValueError):
        if check_only:
            raise
    # Only copy model bytes to a fixed path, never extract archive paths.
    # Temporary files share the destination filesystem for atomic replacement.
    with tempfile.TemporaryDirectory(prefix=f'.{name}-', dir=root) as temporary:
        archive = Path(temporary) / 'download.zip'
        candidate = Path(temporary) / f'{name}.onnx'
        print(f'Downloading {name}: {spec["url"]}', flush=True)
        with urllib.request.urlopen(spec['url'], timeout=60) as response, archive.open('wb') as output:
            while chunk := response.read(CHUNK_BYTES):
                output.write(chunk)
        with zipfile.ZipFile(archive) as zipped:
            members = [entry for entry in zipped.infolist()
                       if not entry.is_dir() and entry.filename.lower().endswith('.onnx')]
            if len(members) != 1:
                raise ValueError(f'{name}: 归档必须恰好包含一个 ONNX 文件，实际 {len(members)} 个')
            if members[0].file_size != spec['bytes']:
                raise ValueError(f'{name}: 归档模型字节数与 manifest 不匹配')
            with zipped.open(members[0]) as source, candidate.open('wb') as output:
                count = 0
                while chunk := source.read(CHUNK_BYTES):
                    count += len(chunk)
                    if count > spec['bytes']:
                        raise ValueError(f'{name}: 模型超过 manifest 字节数')
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        verify_file(candidate, spec)
        os.replace(candidate, destination)
    return 'downloaded'


def main(argv=None, *, root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', choices=MODELS, help='仅处理一个模型；默认处理两者')
    parser.add_argument('--check', action='store_true', help='仅按 manifest 校验现有文件，不联网或修改文件')
    args = parser.parse_args(argv)
    names = (args.model,) if args.model else MODELS
    try:
        manifest = load_manifest(root, names)
        for name, spec in manifest.items():
            result = ensure_model(root, name, spec, check_only=args.check)
            print(f'{name}: {result}; SHA-256 和字节数均符合 manifest', flush=True)
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        parser.exit(1, f'模型校验/下载失败：{exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
