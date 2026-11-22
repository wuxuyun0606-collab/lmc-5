"""Offline verification of the retained October sources and the published snapshot."""
import argparse
import ast
import hashlib
import json
import subprocess
from pathlib import Path


SNAPSHOT = '4f72877621eed555e17c72db6e1a40bc7c762855'
MODULE = Path(__file__).resolve().parent


def historical_dump(node):
    """Serialize the fields present in the original Python 3.9 AST dump."""
    if isinstance(node, list):
        return '[' + ', '.join(historical_dump(item) for item in node) + ']'
    if not isinstance(node, ast.AST):
        return repr(node)
    fields = []
    for name, value in ast.iter_fields(node):
        if name == 'type_params' and value == []:
            continue  # Added in 3.12. Nonempty type parameters are a real change.
        if value is None and not (isinstance(node, ast.Constant) and name == 'value'):
            continue
        fields.append(f'{name}={historical_dump(value)}')
    return type(node).__name__ + '(' + ', '.join(fields) + ')'


def function_class_sha256(source):
    tree = ast.parse(source)
    nodes = [historical_dump(node) for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    return hashlib.sha256(json.dumps(nodes, ensure_ascii=False).encode()).hexdigest()


def verify(source_root=None):
    manifest = json.loads((MODULE / 'coding-source-20261025.json').read_text())
    failures = []
    for entry in manifest['files']:
        path = entry['published_path']
        if source_root is None:
            result = subprocess.run(['git', 'show', f'{SNAPSHOT}:{path}'], cwd=MODULE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode:
                raise SystemExit(f'Historical commit {SNAPSHOT} unavailable. '
                                 'Pass --source-root pointing to its extracted snapshot.')
            raw = result.stdout
        else:
            raw = (Path(source_root) / path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry['published_sha256']:
            failures.append(f'{path}: published digest mismatch')
        expected = entry.get('historical_function_class_ast_sha256')
        if expected and function_class_sha256(raw.decode()) != expected:
            failures.append(f'{path}: historical function/class mismatch')
    if failures:
        raise SystemExit('\n'.join(failures))
    return {'verified_files': len(manifest['files']), 'status': 'matched',
            'source': SNAPSHOT if source_root is None else str(source_root)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path,
                        help='Verify files in this directory instead of the historical Git commit.')
    print(json.dumps(verify(parser.parse_args().source_root)))
