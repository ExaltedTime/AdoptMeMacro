"""Static checks pyflakes does not make. Fails (exit 1) on:

- a `cfg.NAME` used by main.py or bot/ that magic_numbers.py doesn't define - a typo in a constant's name, which would otherwise only show when
  that line runs, possibly hours into a run;
- a constant in magic_numbers.py that nothing uses (not main.py, bot/, tools/, tests/, nor another constant), or a function or method of main.py or
  bot/ that nothing but tests calls: dead code, and the comments and docs that still describe it;
- a test that asks for tmp_path, monkeypatch, caplog or capsys and doesn't use it.

(Unused imports and unused local variables are pyflakes's.)

Needs nothing installed: run `python tools/check_constants.py`."""
import ast
import glob
import io
import os
import re
import sys
import tokenize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = ["main.py", *sorted(glob.glob(os.path.join("bot", "*.py")))]            # what uses the constants
OTHER = [*sorted(glob.glob(os.path.join("tools", "*.py"))), *sorted(glob.glob(os.path.join("tests", "*.py")))]
ENTRY_POINTS = {"main", "build", "setup_hook", "on_ready", "on_error", "interaction_check", "on_timeout", "run", "emit"}   # called by a framework, not by name


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def defined_names(path):
    names = {}
    for node in ast.parse(read(path)).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        for target in targets:
            for part in ast.walk(target):
                if isinstance(part, ast.Name):
                    names[part.id] = node.lineno
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names[node.name] = node.lineno
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update({(a.asname or a.name).split(".")[0]: node.lineno for a in node.names})
    return names


def code_tokens(path):
    """The names in a file's code (not its comments), and its strings - a constant can be named in a string (a monkeypatch, a regex)."""
    names, strings = [], []
    for token in tokenize.generate_tokens(io.StringIO(read(path)).readline):
        if token.type == tokenize.NAME:
            names.append(token.string)
        elif token.type == tokenize.STRING:
            strings.append(token.string)
    return names, strings


def unknown_constants(defined):
    problems = []
    for path in CODE:
        for node in ast.walk(ast.parse(read(path))):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "cfg" and node.attr not in defined):
                problems.append((node.lineno, path, f"cfg.{node.attr} is not defined in magic_numbers.py"))
    return problems


def unused_constants(defined):
    own_names, _ = code_tokens("magic_numbers.py")
    used = {name for name in own_names if own_names.count(name) > 1}                    # one use besides its definition
    text = "\n".join(read(path) for path in (*CODE, *OTHER))
    return [(line, "magic_numbers.py", f"{name} is not used anywhere")
            for name, line in defined.items() if name == name.upper() and name not in used and not re.search(rf"\b{re.escape(name)}\b", text)]


def unused_functions():
    """Functions and methods of main.py and bot/ whose name appears nowhere else in main.py or bot/ (a test that calls one does not count)."""
    words = {}
    for path in CODE:
        names, strings = code_tokens(path)
        for name in names:
            words[name] = words.get(name, 0) + 1
        for string in strings:
            name = string.strip("\"'")
            if re.fullmatch(r"[A-Za-z_]\w*", name):                                      # (a string that is a name: getattr, a dispatch table)
                words[name] = words.get(name, 0) + 1
    problems = []
    for path in CODE:
        for node in ast.walk(ast.parse(read(path))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("__") and node.name not in ENTRY_POINTS and words[node.name] <= 1:
                if not node.decorator_list:                                              # (a decorated one - a command, a button - is registered by its decorator)
                    problems.append((node.lineno, path, f"{node.name}() is not called anywhere in the code"))
    return problems


def unused_test_parameters():
    """A test that asks for tmp_path, monkeypatch, caplog or capsys and never uses it (the other fixtures can matter by existing: a `macro` sets things up)."""
    problems = []
    for path in sorted(glob.glob(os.path.join("tests", "test_*.py"))):
        for node in ast.walk(ast.parse(read(path))):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                for argument in node.args.args:
                    if argument.arg in ("tmp_path", "monkeypatch", "caplog", "capsys") and argument.arg not in used:
                        problems.append((node.lineno, path, f"{node.name}() asks for {argument.arg} and does not use it"))
    return problems


def main():
    defined = defined_names("magic_numbers.py")
    problems = unknown_constants(defined) + unused_constants(defined) + unused_functions() + unused_test_parameters()
    for line, path, message in sorted(problems):
        print(f"{path}:{line}: {message}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
