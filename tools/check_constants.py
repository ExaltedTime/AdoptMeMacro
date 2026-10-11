"""Fails (exit 1) if main.py uses a cfg.NAME that magic_numbers.py doesn't
define - a typo in a constant's name. pyflakes can't see this (it doesn't check
what a module has), and it would otherwise only show when that line runs,
possibly hours into a run. Needs nothing installed: run `python tools/check_constants.py`."""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def defined_names(path):
    names = set()
    for node in ast.parse(open(path, encoding="utf-8").read()).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        for target in targets:
            for part in ast.walk(target):
                if isinstance(part, ast.Name):
                    names.add(part.id)
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
    return names


def main():
    defined = defined_names(os.path.join(ROOT, "magic_numbers.py"))
    tree = ast.parse(open(os.path.join(ROOT, "main.py"), encoding="utf-8").read())
    unknown = sorted({(node.lineno, node.attr) for node in ast.walk(tree)
                      if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                      and node.value.id == "cfg" and node.attr not in defined})
    for line, name in unknown:
        print(f"main.py:{line}: cfg.{name} is not defined in magic_numbers.py")
    return 1 if unknown else 0


if __name__ == "__main__":
    sys.exit(main())
