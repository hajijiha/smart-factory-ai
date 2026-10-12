"""Validate documented Python definitions and Conventional Commits on the submitted history."""
import argparse
import ast
import json
import re
import subprocess
from pathlib import Path


def audit(root, check_git=True):
    """Return all missing docstrings and commit-format violations without changing sources."""
    missing = []
    definitions = 0
    for folder in ('factory', 'simulator', 'scripts', 'tests'):
        for file in sorted((root / folder).rglob('*.py')):
            for node in ast.walk(ast.parse(file.read_text(encoding='utf-8-sig'))):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    definitions += 1
                    if ast.get_docstring(node) is None:
                        missing.append({'file': file.relative_to(root).as_posix(), 'line': node.lineno, 'name': node.name})
    invalid = []
    if check_git:
        output = subprocess.check_output(['git', '-C', str(root), 'log', '--format=%h%x09%s'], text=True)
        invalid = [line for line in output.splitlines() if not re.match(r'^[a-zA-Z][a-zA-Z0-9_-]*(?:\([^()]+\))?!?: .+', line.split('\t', 1)[1])]
    return {'passed': not missing and not invalid, 'definitions': definitions, 'missing_docstrings': missing,
            'nonconventional_commits': invalid, 'git_checked': check_git}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--no-git', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = audit(args.root, not args.no_git)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
