"""A source distribution must produce a wheel usable outside its checkout."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(code: str, cwd: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("STEP2AAS_MAPPING_DIR", None)
    return subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_sdist_wheel_contains_canonical_rules_and_loads_in_isolation(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("pyproject.toml", "setup.py", "MANIFEST.in", "README.md", "LICENSE"):
        shutil.copy2(ROOT / name, source / name)
    shutil.copytree(
        ROOT / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info")
    )
    shutil.copytree(ROOT / "mapping", source / "mapping")
    _run("from setuptools.build_meta import build_sdist; build_sdist('dist')", source)
    archives = list((source / "dist").glob("*.tar.gz"))
    assert len(archives) == 1
    unpacked = tmp_path / "sdist"
    with tarfile.open(archives[0]) as archive:
        archive.extractall(unpacked, filter="data")
    build_root = next(unpacked.iterdir())
    _run("from setuptools.build_meta import build_wheel; build_wheel('dist')", build_root)
    wheels = list((build_root / "dist").glob("*.whl"))
    assert len(wheels) == 1
    installed = tmp_path / "installed"
    rules = sorted((ROOT / "mapping").glob("*.yaml"))
    assert rules
    with zipfile.ZipFile(wheels[0]) as wheel:
        for rule in rules:
            assert wheel.read(f"step2aas/mapping/{rule.name}") == rule.read_bytes()
        wheel.extractall(installed)
    outside = tmp_path / "outside"
    outside.mkdir()
    result = _run(
        "import sys, pathlib, warnings; "
        f"sys.path.insert(0, {str(installed)!r}); "
        "from step2aas.mapping.rules import load, RULES_DIR; "
        "from step2aas.compat import ObjectStore; "
        "warnings.simplefilter('error', DeprecationWarning); ObjectStore(); "
        f"assert RULES_DIR == pathlib.Path({str(installed / 'step2aas/mapping')!r}); "
        f"assert all(load(n)['submodel']['idShort'] for n in {[p.stem for p in rules]!r}); "
        "print('isolated wheel rules and SDK store verified')",
        outside,
    )
    assert "isolated wheel rules" in result.stdout
    (tmp_path / "package-check.json").write_text(
        json.dumps({"rules": [p.name for p in rules], "isolated_import": True}), encoding="utf-8"
    )
