"""Viewer left-nav tree is reconstructed from 02011 BOM, not stored in the AAS."""

from __future__ import annotations

from basyx.aas import model

from step2aas.compat import ObjectStore
from step2aas.mapping.bom import build_bom
from step2aas.model import PartNode
from step2aas.viewer import build_html


def _tree() -> PartNode:
    a1 = PartNode(name="A1", product_id="a", ref_key="a")
    a2 = PartNode(name="A2", product_id="a", ref_key="a")
    b = PartNode(name="B", product_id="b", ref_key="b")
    sub = PartNode(name="Sub", product_id="s", ref_key="s", children=[b])
    return PartNode(name="Asm", product_id="asm", ref_key="asm", children=[a1, a2, sub])


def _asset(part: PartNode) -> str:
    return f"urn:asset:{part.ref_key}"


def _shell(id_short: str, asset_id: str, *submodels) -> model.AssetAdministrationShell:
    return model.AssetAdministrationShell(
        id_=f"urn:aas:{id_short}",
        id_short=id_short,
        display_name={"en": id_short},
        asset_information=model.AssetInformation(
            asset_kind=model.AssetKind.TYPE,
            global_asset_id=asset_id,
        ),
        submodel={model.ModelReference.from_referable(sm) for sm in submodels},
    )


def _store(*objects) -> ObjectStore:
    store = ObjectStore()
    for obj in objects:
        store.add(obj)
    return store


def _nav(html: str) -> str:
    start = html.index("<nav>") + len("<nav>")
    end = html.index("</nav>")
    return html[start:end]


def test_hierarchical_nav_nests_subassembly_under_root():
    root = _tree()
    bom_asm = build_bom(root, "urn:sm:bom-asm", "urn:asset:asm", _asset, recurse=False, linked=True)
    bom_sub = build_bom(
        root.children[2], "urn:sm:bom-sub", "urn:asset:s", _asset, recurse=False, linked=True
    )
    store = _store(
        bom_asm,
        bom_sub,
        _shell("Asm", "urn:asset:asm", bom_asm),
        _shell("Sub", "urn:asset:s", bom_sub),
        _shell("A", "urn:asset:a"),
        _shell("B", "urn:asset:b"),
    )
    nav = _nav(build_html("asm.aasx", store, [], {}))

    assert "A1" in nav and "A2" in nav and "B" in nav
    assert nav.index("Asm") < nav.index("Sub") < nav.index("B")
    assert nav.count('class="nav-leaf"') == 3  # A1, A2, B
    assert nav.count('class="nav-asm"') == 2  # Asm, Sub
    # Sub is nested under Asm, not a leftover root sibling.
    sub_pos = nav.index(">Sub</a>")
    assert nav.rfind('class="nav-kids"', 0, sub_pos) != -1


def test_flat_nav_expands_comanaged_subassembly_inline():
    root = _tree()
    bom = build_bom(root, "urn:sm:bom", "urn:asset:asm", _asset, recurse=True, linked=True)
    store = _store(
        bom,
        _shell("Asm", "urn:asset:asm", bom),
        _shell("A", "urn:asset:a"),
        _shell("B", "urn:asset:b"),
    )
    nav = _nav(build_html("asm.aasx", store, [], {}))
    assert "Sub" in nav and "B" in nav
    assert nav.index("Sub") < nav.index("B")
    assert nav.rfind('class="nav-kids"', 0, nav.index("B")) != -1


def test_global_asset_links_determine_root_order_without_sameas():
    root = _tree()
    root.name = "ZRoot"
    root.children[2].name = "ASub"
    root_bom = build_bom(root, "urn:sm:root", "urn:asset:asm", _asset, recurse=False, linked=True)
    sub_bom = build_bom(
        root.children[2], "urn:sm:sub", "urn:asset:s", _asset, recurse=False, linked=True
    )
    store = _store(
        root_bom,
        sub_bom,
        _shell("ZRoot", "urn:asset:asm", root_bom),
        _shell("ASub", "urn:asset:s", sub_bom),
        _shell("A", "urn:asset:a"),
        _shell("B", "urn:asset:b"),
    )
    nav = _nav(build_html("asm.aasx", store, [], {}))
    assert nav.index("ZRoot") < nav.index("ASub")
    assert nav.count("⛓ ") == 1
    assert nav.count('class="nav-asm"') == 2
