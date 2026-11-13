"""Verify the published coding snapshot against its reconciliation manifest.

Does not import service modules, read credentials, open databases or use network.
The historical AST digests are recorded by the November reconciliation; they
are not independent evidence of when those source files were deployed.
"""
import ast
import hashlib
import json
from pathlib import Path


def function_class_sha256(source):
    tree = ast.parse(source)
    nodes = [ast.dump(node, include_attributes=False) for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    return hashlib.sha256(json.dumps(nodes, ensure_ascii=False).encode()).hexdigest()


def verify():
    module = Path(__file__).resolve().parent
    root = module.parents[1]
    manifest = json.loads((module / 'coding-source-20261025.json').read_text())
    failures = []
    for entry in manifest['files']:
        path = root / entry['published_path']
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry['published_sha256']:
            failures.append(entry['published_path'] + ': published digest mismatch')
        expected = entry.get('historical_function_class_ast_sha256')
        if expected and function_class_sha256(raw.decode()) != expected:
            failures.append(entry['published_path'] + ': historical function/class mismatch')
    if failures:
        raise SystemExit('\n'.join(failures))
    return {'verified_files': len(manifest['files']), 'status': 'matched'}


if __name__ == '__main__':
    print(json.dumps(verify()))
