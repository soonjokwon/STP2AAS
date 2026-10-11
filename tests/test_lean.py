"""Lean packaging options (D9): AAS only for the root and repeated designs
(lean-hierarchical) or repeated parts (lean-flat).

Checked with the package-only reader (scripts/reconstruct_aasx.py), which does
not import the converter: Rules 1 and 2 must give the same answers as the
corresponding full option, with fewer AAS.
"""

import zipfile

import pytest

pytest.importorskip("OCC")

from scripts import reconstruct_aasx as rx
from scripts.make_test_fixtures import make_cart_assembly
from step2aas.aasx_writer import write_aasx
from step2aas.extract import extract_document

MODES = ("hierarchical", "flat", "single", "lean-hierarchical", "lean-flat")
# mode -> (AAS, BOMs, designs visible as one design, part uses with own entry, STEP files)
EXPECTED = {
    "hierarchical": (7, 3, 3, 2, 7),
    "flat": (5, 1, 2, 6, 5),
    "single": (1, 1, 0, 6, 5),
    "lean-hierarchical": (4, 2, 3, 2, 6),
    "lean-flat": (3, 1, 2, 6, 5),
}


@pytest.fixture(scope="module")
def packages(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cart")
    source = tmp / "cart.step"
    make_cart_assembly(source)
    out = {}
    for mode in MODES:
        package = tmp / f"{mode}.aasx"
        write_aasx(extract_document(str(source)), str(package), assembly_structure=mode)
        out[mode] = package
    return out


@pytest.mark.parametrize("mode", MODES)
def test_counts_follow_rules(packages, mode):
    pkg = rx.load(str(packages[mode]))
    st = rx.stats(pkg)
    with zipfile.ZipFile(packages[mode]) as archive:
        n_step = sum(n.lower().endswith(".stp") for n in archive.namelist())
    got = (st["shells"], st["boms"], st["shared_gids"], st["unique_node_leaf_occ"], n_step)
    assert got == EXPECTED[mode]
    assert st["leaf_occurrences"] == 6
    assert st["scene_instances"] == 6 and st["scene_keyed"]


def test_same_tree_in_every_mode(packages):
    canon = {m: rx.stats(rx.load(str(p)))["canon"] for m, p in packages.items()}
    assert len(set(canon.values())) == 1


@pytest.mark.parametrize("mode", ("lean-hierarchical", "lean-flat"))
def test_parts_without_aas_keep_their_geometry(packages, mode):
    """Frame and Grip have no AAS; their STEP files hang off the root AAS and the
    scene names them, so the assembly can still be rebuilt from the package."""
    pkg = rx.load(str(packages[mode]))
    root_scene = max(pkg.scenes.values(), key=lambda s: len(s["instances"]))
    shell_gids = set(pkg.shells)
    with zipfile.ZipFile(packages[mode]) as archive:
        names = {"/" + n for n in archive.namelist()}
    loose = [i for i in root_scene["instances"] if i["asset"] not in shell_gids]
    assert len(loose) == 2
    for inst in loose:
        assert inst["file"] in names
