"""The checks in tools/: they pass on the repo as it is (CI runs them too), and the constants check finds what it is for."""
import subprocess
import sys

import conftest


def run_check():
    return subprocess.run([sys.executable, "tools/check_constants.py"], cwd=conftest.ROOT, capture_output=True, text=True)


def test_the_constants_check_passes_on_the_repo():
    result = run_check()
    assert result.returncode == 0, result.stdout


def test_the_constants_check_names_an_unused_constant_a_typo_and_an_uncalled_function(tmp_path, monkeypatch):
    sys.path.insert(0, conftest.ROOT + "/tools")
    try:
        import check_constants
    finally:
        sys.path.pop(0)
    (tmp_path / "bot").mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "magic_numbers.py").write_text("USED = 1\nUNUSED = 2\nCOMBINED = USED + 1\n")
    (tmp_path / "main.py").write_text("import magic_numbers as cfg\n\ndef used():\n    return cfg.COMBINED + cfg.TYPO\n\ndef lonely():\n    return used()\n")
    monkeypatch.setattr(check_constants, "ROOT", str(tmp_path))
    monkeypatch.setattr(check_constants, "CODE", ["main.py"])
    monkeypatch.setattr(check_constants, "OTHER", [])
    defined = check_constants.defined_names("magic_numbers.py")
    assert [m for _, _, m in check_constants.unknown_constants(defined)] == ["cfg.TYPO is not defined in magic_numbers.py"]
    assert [m for _, _, m in check_constants.unused_constants(defined)] == ["UNUSED is not used anywhere"]
    assert [m for _, _, m in check_constants.unused_functions()] == ["lonely() is not called anywhere in the code"]
