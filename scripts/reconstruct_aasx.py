"""Rebuild the occurrence-prototype graph from an AASX package.

Independent of the converter: reads only the serialized XML (data.xml) and the
PlacementScene JSON files, using zipfile + lxml. No step2aas or BaSyx imports.

Expansion rule (what an AAS consumer can do with the package alone):
  * start at the root shell's BOM EntryNode;
  * a Node with child Entities is expanded inline (Full ArcheType);
  * a SelfManaged Node without children whose globalAssetId names a shell that
    has its own BOM is expanded through that BOM (OneDown);
  * otherwise the Node is a leaf occurrence.
A prototype is "identifier-expressed" when its occurrences carry the same
globalAssetId (AASd-014 forbids asset ids on CoManaged Entities).
"""
from __future__ import annotations

import collections
import json
import sys
import zipfile
from dataclasses import dataclass, field

from lxml import etree


@dataclass
class Occ:
    gid: str | None
    kind: str
    node_ref: tuple[str, str]  # (BOM submodel id, idShort path)
    children: list["Occ"] = field(default_factory=list)


@dataclass
class Package:
    shells: dict  # globalAssetId -> {"id":..., "submodels":[...]}
    boms: dict  # submodel id -> (archetype, entry element)
    bom_of_asset: dict  # globalAssetId -> BOM submodel id
    scenes: dict  # file name -> scene json
    root_gid: str
    ns: dict
    sizes: dict


def _t(el, path, ns):
    return el.findtext(path, namespaces=ns)


def load(path: str) -> Package:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        xml_name = next(n for n in names if n.endswith("data.xml"))
        root = etree.fromstring(z.read(xml_name))
        scenes = {n: json.loads(z.read(n)) for n in names if n.endswith("__scene.json")}
        sizes = json.loads(z.read("_sizes.json")) if "_sizes.json" in names else {
            n: z.getinfo(n).file_size for n in names}
    ns = {"a": etree.QName(root).namespace}
    shells = {}
    for aas in root.findall("a:assetAdministrationShells/a:assetAdministrationShell", ns):
        gid = _t(aas, "a:assetInformation/a:globalAssetId", ns)
        sms = [_t(r, "a:keys/a:key/a:value", ns) for r in aas.findall("a:submodels/a:reference", ns)]
        shells[gid] = {"id": _t(aas, "a:id", ns), "submodels": sms}
    boms, bom_of_asset = {}, {}
    sm_by_id = {_t(sm, "a:id", ns): sm for sm in root.findall("a:submodels/a:submodel", ns)}
    for gid, sh in shells.items():
        for sid in sh["submodels"]:
            sm = sm_by_id.get(sid)
            if sm is None:
                continue
            entry = sm.find("a:submodelElements/a:entity", ns)
            if entry is not None and _t(entry, "a:idShort", ns) == "EntryNode":
                arche = next((_t(p, "a:value", ns) for p in sm.findall("a:submodelElements/a:property", ns)
                              if _t(p, "a:idShort", ns) == "ArcheType"), None)
                boms[sid] = (arche, entry)
                bom_of_asset[gid] = sid
    referenced = set()
    for _sid, (_a, entry) in boms.items():
        for e in entry.iter(f"{{{ns['a']}}}entity"):
            if e is not entry:
                g = _t(e, "a:globalAssetId", ns)
                if g:
                    referenced.add(g)
    roots = [g for g in bom_of_asset if g not in referenced] or list(shells)
    return Package(shells, boms, bom_of_asset, scenes, roots[0], ns, sizes)


def expand(pkg: Package, gid: str | None = None, _stack=()) -> Occ:
    ns = pkg.ns
    gid = gid or pkg.root_gid
    sid = pkg.bom_of_asset.get(gid)
    root = Occ(gid, "SelfManagedEntity", (sid or "", "EntryNode"))
    if sid is None:
        return root

    def kids(el, prefix):
        return [(c, f"{prefix}.{_t(c, 'a:idShort', ns)}") for c in el.findall("a:statements/a:entity", ns)]

    def build(el, path, sm_id) -> Occ:
        g = _t(el, "a:globalAssetId", ns)
        kind = _t(el, "a:entityType", ns)
        occ = Occ(g, kind, (sm_id, path))
        ch = kids(el, path)
        if ch:
            occ.children = [build(c, p, sm_id) for c, p in ch]
        elif kind == "SelfManagedEntity" and g in pkg.bom_of_asset and g not in _stack:
            sub = expand(pkg, g, _stack + (gid,))
            occ.children = sub.children
        return occ

    _a, entry = pkg.boms[sid]
    root.children = [build(c, p, sid) for c, p in kids(entry, "EntryNode")]
    return root


def leaves(o: Occ, path=()):
    if not o.children:
        yield path, o
    for i, c in enumerate(o.children):
        yield from leaves(c, path + (i,))


def canon(o: Occ) -> str:
    """Unlabeled canonical form (AHU) of the occurrence tree."""
    return "(" + "".join(sorted(canon(c) for c in o.children)) + ")"


def stats(pkg: Package) -> dict:
    tree = expand(pkg)
    leaf_list = list(leaves(tree))
    # identifier-expressed sharing: prototypes whose gid is carried by >=2 occurrences
    all_occ = []

    def walk(o, depth, parents_unique):
        for c in o.children:
            all_occ.append((c, depth, parents_unique))
            walk(c, depth + 1, parents_unique and c.gid is not None and True)
    walk(tree, 1, True)
    gid_count = collections.Counter(o.gid for o, _d, _u in all_occ if o.gid)
    leaf_gid = collections.Counter(o.gid for _p, o in leaf_list if o.gid)
    node_count = collections.Counter(o.node_ref for o, _d, _u in all_occ)
    unique_node_occ = sum(1 for o, _d, _u in all_occ if not o.children and node_count[o.node_ref] == 1)
    root_scene = max(pkg.scenes.values(), key=lambda s: len(s["instances"])) if pkg.scenes else None
    return {
        "shells": len(pkg.shells),
        "boms": len(pkg.boms),
        "leaf_occurrences": len(leaf_list),
        "max_depth": max((len(p) for p, _o in leaf_list), default=0),
        "canon": canon(tree),
        "shared_gids": sum(1 for v in gid_count.values() if v >= 2),
        "shared_leaf_gids": sum(1 for v in leaf_gid.values() if v >= 2),
        "leaf_without_gid": sum(1 for _p, o in leaf_list if not o.gid),
        "unique_node_leaf_occ": unique_node_occ if tree.children else 1,
        "scene_instances": len(root_scene["instances"]) if root_scene else 0,
        "scene_keyed": bool(root_scene) and all("node" in i for i in root_scene["instances"]),
    }


if __name__ == "__main__":
    for p in sys.argv[1:]:
        s = stats(load(p))
        s.pop("canon")
        print(p, json.dumps(s))
