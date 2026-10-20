"""Static checks pyflakes does not make. Fails (exit 1) on:

- a `cfg.NAME` used by main.py or bot/ that magic_numbers.py doesn't define - a typo in a constant's name, which would otherwise only show when
  that line runs, possibly hours into a run;
- a constant in magic_numbers.py that nothing uses (not main.py, bot/, tools/, tests/, nor another constant), or a function or method of main.py or
  bot/ that nothing but tests calls: dead code, and the comments and docs that still describe it;
- a constant the docs name in backticks that no code file has;
- a key table label (`cfg.KEYS["x"]`) that does not exist, or a key written as a letter where the code presses one;
- a `pydirectinput.keyDown`/`keyUp` in main.py outside the wrappers that keep track of the keys held;
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


def files(pattern):
    """The files matching `pattern` (relative to the repo), as paths relative to it."""
    return sorted(os.path.relpath(path, ROOT) for path in glob.glob(os.path.join(ROOT, pattern), recursive=True))


CODE = ["main.py", *files("bot/*.py")]                                         # what uses the constants
OTHER = [*files("tools/*.py"), *files("tests/*.py")]
EXTERNAL_NAMES = {"TM_CCOEFF_NORMED", "SIGKILL", "HALLOWEEN", "WS_EX_TRANSPARENT", "WS_EX_NOACTIVATE", "WDA_EXCLUDEFROMCAPTURE"}                       # named in the docs, from a library or the OS
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


def key_labels():
    """The labels of the key tables of magic_numbers.py (KEYS, KEY_SEQUENCES, HOTKEYS), read without importing it."""
    labels = {}
    for node in ast.parse(read("magic_numbers.py")).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ("KEYS", "KEY_SEQUENCES", "HOTKEYS"):
            labels[node.targets[0].id] = {key.value for key in node.value.keys if isinstance(key, ast.Constant)}   # type: ignore[attr-defined]
    return labels


def key_problems():
    """A cfg.KEYS["x"] (or KEY_SEQUENCES, HOTKEYS) with a label the table does not have, and a key written as a letter where the code presses one
    (hold_key("a", ...), key_down("w"), press("e")): every key is named by what it is for, in the tables."""
    labels, problems = key_labels(), []
    for path in CODE:
        for node in ast.walk(ast.parse(read(path))):
            if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr in labels
                    and isinstance(node.slice, ast.Constant) and node.slice.value not in labels[node.value.attr]):
                problems.append((node.lineno, path, f"{node.value.attr}[{node.slice.value!r}] is not in the table"))
            if (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                    and ((isinstance(node.func, ast.Name) and node.func.id in ("hold_key", "key_down", "key_up"))
                         or (isinstance(node.func, ast.Attribute) and node.func.attr == "press"))):
                problems.append((node.lineno, path, f"a key written as {node.args[0].value!r}: use cfg.KEYS, cfg.KEY_SEQUENCES or cfg.HOTKEYS"))
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


def unknown_names_in_docs():
    """A constant (or other UPPER_CASE name) the docs write in backticks that no code file has: a renamed or removed one the docs still describe."""
    known = set(EXTERNAL_NAMES)
    for path in (*CODE, *OTHER, "magic_numbers.py"):
        known.update(code_tokens(path)[0])
    problems = []
    for path in files("docs/**/*.md"):
        if os.path.basename(path) == "review.md":                              # (a record of findings: it names what was found wrong, and what has since gone)
            continue
        for number, line in enumerate(read(path).split("\n"), 1):
            for name in re.findall(r"`([A-Z][A-Z0-9_]{3,})`", line):
                if name not in known:
                    problems.append((number, path, f"{name} is named in the docs and exists in no code file"))
    return problems


def bare_key_calls():
    """pydirectinput.keyDown or keyUp in main.py outside key_down(), key_up() and release_all_inputs(): a key pressed that way is not known to release_all_inputs()."""
    problems = []
    tree = ast.parse(read("main.py"))
    allowed = {"key_down", "key_up", "release_all_inputs"}
    for function in ast.walk(tree):
        if isinstance(function, ast.FunctionDef) and function.name not in allowed:
            for node in ast.walk(function):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("keyDown", "keyUp")
                        and isinstance(node.func.value, ast.Name) and node.func.value.id == "pydirectinput"):
                    problems.append((node.lineno, "main.py", f"pydirectinput.{node.func.attr}() in {function.name}(): use key_down() and key_up()"))
    return problems


def unused_test_parameters():
    """A test that asks for tmp_path, monkeypatch, caplog or capsys and never uses it (the other fixtures can matter by existing: a `macro` sets things up)."""
    problems = []
    for path in files("tests/test_*.py"):
        for node in ast.walk(ast.parse(read(path))):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                for argument in node.args.args:
                    if argument.arg in ("tmp_path", "monkeypatch", "caplog", "capsys") and argument.arg not in used:
                        problems.append((node.lineno, path, f"{node.name}() asks for {argument.arg} and does not use it"))
    return problems


def main():
    defined = defined_names("magic_numbers.py")
    problems = unknown_constants(defined) + unused_constants(defined) + unused_functions() + unused_test_parameters() + unknown_names_in_docs() + bare_key_calls() + key_problems()
    for line, path, message in sorted(problems):
        print(f"{path}:{line}: {message}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
