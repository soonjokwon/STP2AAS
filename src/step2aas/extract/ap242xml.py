"""AP242 Domain Model XML (BOM) input → the same PartNode/StepDocument IR.

Handles the CAx-IF / MBx-IF "AP242 Domain Model XML Assembly Structure"
(ISO 10303-4442 ed-3, e.g. Datakit .stpx exports). Two layouts are supported:

* all-in-one: one XML holds the whole assembly tree; piece parts reference
  external STEP geometry files.
* nested:     the tree is split across files — an assembly references child
  sub-assembly .stpx files, which we recurse into.

Producing the same intermediate representation as extract/xde.py lets every
downstream mapper (§2 BOM, §3 TechnicalData, §4 Models3D) and the writer be
reused. Structure (see STP2X3D AP242XML_Reader.cpp):

  Part → PartVersion → PartView                       (a product + its definition)
  assembly PartView → ViewOccurrenceRelationship (NAUO) → Related=Occurrence
      + Placement/CartesianTransformation             (child instance + placement, S2)
  the Related Occurrence is contained in the CHILD's PartView → child Part
  piece-part PartView → DocumentAssignment → File → (external file name)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
from urllib.parse import quote

from lxml import etree

from step2aas.extract.xde import load_geometry
from step2aas.model import P21Header, PartNode, PhysicalProps, Provenance, StepDocument

logger = logging.getLogger("step2aas.ap242xml")

_MAX_DEPTH = 20
_STEP_EXT = {".stp", ".step", ".stpz"}
_XML_EXT = {".stpx", ".xml"}
_IDENTITY_SCHEME = "ap242-dependency-v2"


def extract_ap242_xml(path: str) -> StepDocument:
    ctx = _Context(path)
    root = ctx.build_file(path, transform=None, instance_name=None, depth=0, visiting=frozenset())
    header = _Ap242Reader(path).header()  # header always from the top file
    manifest = ctx.manifest()
    # D5: both XML structure and external geometry are part of the version basis.
    identity = json.dumps(
        {"identity_scheme": _IDENTITY_SCHEME, "sources": manifest},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return StepDocument(
        root=root,
        header=header,
        source_path=path,
        file_hash=hashlib.sha256(identity).hexdigest(),
        source_manifest=manifest,
        identity_scheme=_IDENTITY_SCHEME,
        legacy_file_hash=_file_hash(path),
    )


def _ln(el) -> str:
    return etree.QName(el).localname


class _Context:
    """Shared state across (possibly nested) AP242 XML files."""

    def __init__(self, top_path: str) -> None:
        self.base_dir = pathlib.Path(top_path).resolve().parent
        self.geo_cache: dict[str, tuple] = {}
        self.dependencies: dict[tuple[str, str], dict[str, str]] = {}

    def relative_path(self, path: pathlib.Path) -> str:
        # D2/D5: names are relative to the top XML directory, independent of checkout.
        return pathlib.Path(os.path.relpath(path.resolve(), self.base_dir)).as_posix()

    def xml_key(self, path: pathlib.Path, uid: str) -> str:
        return f"xml:{quote(self.relative_path(path), safe='/')}#{quote(uid, safe='')}"

    def file_key(self, path: pathlib.Path) -> str:
        return f"file:{quote(self.relative_path(path), safe='/')}"

    def record_dependency(self, path: pathlib.Path, role: str) -> None:
        relative = self.relative_path(path)
        key = (relative, role)
        if key not in self.dependencies:
            present = path.is_file()
            self.dependencies[key] = {
                "path": relative,
                "role": role,
                "status": "present" if present else "missing",
                "sha256": _file_hash(str(path)) if present else "",
            }

    def manifest(self) -> list[dict[str, str]]:
        return [self.dependencies[key].copy() for key in sorted(self.dependencies)]

    def build_file(self, path, transform, instance_name, depth, visiting) -> PartNode:
        self.record_dependency(pathlib.Path(path), "xml")
        reader = _Ap242Reader(path)
        node = reader.build_tree(self, depth, visiting | {str(pathlib.Path(path).resolve())})
        # The occurrence that referenced this file owns the placement + instance name.
        if transform is not None:
            node.transform = transform
        if instance_name:
            node.name = instance_name
        return node

    def load_geometry(self, stp_path: str):
        if stp_path not in self.geo_cache:
            self.geo_cache[stp_path] = load_geometry(stp_path)
        return self.geo_cache[stp_path]


class _Ap242Reader:
    def __init__(self, path: str) -> None:
        self.path = path
        self.stem = pathlib.Path(path).stem
        self.dir = pathlib.Path(path).resolve().parent
        self.root_el = etree.parse(path).getroot()
        self.by_uid: dict[str, etree._Element] = {}
        for el in self.root_el.iter():
            uid = el.attrib.get("uid")
            if uid:
                self.by_uid[uid] = el
        self._index_parts()

    # --- indexing -----------------------------------------------------------

    def _index_parts(self) -> None:
        self.part_of_pv: dict[str, str] = {}  # PartView uid -> Part uid
        self.pv_of_occ: dict[str, str] = {}  # Occurrence uid -> containing PartView uid
        self.parts: dict[str, dict] = {}  # Part uid -> info

        for part in self.root_el.iter():
            if _ln(part) != "Part":
                continue
            part_uid = part.attrib.get("uid")
            name_el = part.find(".//{*}Name/{*}CharacterString")
            type_el = part.find(".//{*}PartTypes/{*}ClassString")
            is_assembly = type_el is not None and "assembly" in (type_el.text or "").lower()
            pvs = part.findall(".//{*}PartView")
            self.parts[part_uid] = {
                "name": (name_el.text or "").strip() if name_el is not None else part_uid,
                "is_assembly": is_assembly,
                "pv_uids": [pv.attrib.get("uid") for pv in pvs],
            }
            for pv in pvs:
                self.part_of_pv[pv.attrib.get("uid")] = part_uid

        for occ in self.root_el.iter():
            if _ln(occ) != "Occurrence":
                continue
            pv = occ.getparent()
            while pv is not None and _ln(pv) != "PartView":
                pv = pv.getparent()
            if pv is not None:
                self.pv_of_occ[occ.attrib.get("uid")] = pv.attrib.get("uid")

    # --- tree construction --------------------------------------------------

    def build_tree(self, ctx: _Context, depth: int, visiting: frozenset) -> PartNode:
        if not self.parts:
            raise ValueError(f"AP242 XML {self.path}: no Part elements")
        edges = self._edges()
        children_parts = {c for kids in edges.values() for (c, _, _) in kids}
        roots = [
            uid
            for uid, info in self.parts.items()
            if info["is_assembly"] and uid not in children_parts
        ]
        if not roots:
            roots = [uid for uid in self.parts if uid not in children_parts] or list(self.parts)
        if len(roots) > 1:
            logger.warning("AP242 XML %s: multiple root candidates; using first", self.stem)
        return self._build(roots[0], edges, None, None, ctx, depth, visiting)

    def _edges(self) -> dict[str, list[tuple[str, list, str]]]:
        edges: dict[str, list[tuple[str, list, str]]] = {}
        for pv in self.root_el.iter():
            if _ln(pv) != "PartView":
                continue
            parent_part = self.part_of_pv.get(pv.attrib.get("uid"))
            if parent_part is None:
                continue
            for vor in pv.findall("{*}ViewOccurrenceRelationship"):
                related = vor.find("{*}Related")
                if related is None:
                    continue
                child_part = self.part_of_pv.get(self.pv_of_occ.get(related.attrib.get("uidRef")))
                if child_part is None:
                    continue
                edges.setdefault(parent_part, []).append(
                    (child_part, self._transform(vor), self._occ_name(related.attrib.get("uidRef")))
                )
        return edges

    def _build(
        self,
        part_uid,
        edges,
        transform,
        instance_name,
        ctx,
        depth,
        visiting,
        ancestors: frozenset = frozenset(),
    ) -> PartNode:
        # S1: only the current path is guarded; repeated prototypes in sibling
        # branches are valid occurrences and must not be mistaken for cycles.
        if part_uid in ancestors:
            raise ValueError(f"AP242 XML {self.path}: part cycle at {part_uid}")
        if len(ancestors) >= _MAX_DEPTH:
            raise ValueError(f"AP242 XML {self.path}: part depth exceeds {_MAX_DEPTH}")
        info = self.parts[part_uid]
        name = instance_name or info["name"]
        child_edges = edges.get(part_uid, [])

        # Assembly defined in this file: recurse over its in-file occurrences.
        if child_edges:
            node = PartNode(
                name=name,
                product_id=f"{self.stem}:{part_uid}",
                ref_key=ctx.xml_key(pathlib.Path(self.path), part_uid),
                transform=transform,
            )
            for child_part, xform, occ_name in child_edges:
                node.children.append(
                    self._build(
                        child_part,
                        edges,
                        xform,
                        occ_name,
                        ctx,
                        depth,
                        visiting,
                        ancestors | {part_uid},
                    )
                )
            return node

        # Otherwise resolve the part's external file reference.
        file_ref = self._resolve_part_file(info, ctx)
        if file_ref is not None:
            ext = file_ref.suffix.lower()
            if ext in _XML_EXT:
                return self._recurse_subassembly(file_ref, name, transform, ctx, depth, visiting)
            if ext in _STEP_EXT:
                return self._leaf_with_geometry(file_ref, name, transform, ctx)

        # No children and no usable file → geometry-less node (gap).
        logger.warning("AP242 XML: no geometry for part %r (gap)", name)
        return PartNode(
            name=name,
            product_id=f"{self.stem}:{part_uid}",
            ref_key=ctx.xml_key(pathlib.Path(self.path), part_uid),
            transform=transform,
            props=PhysicalProps(provenance=Provenance(source="fallback")),
        )

    def _recurse_subassembly(self, file_ref, name, transform, ctx, depth, visiting) -> PartNode:
        resolved = str(file_ref.resolve())
        if depth >= _MAX_DEPTH or resolved in visiting:
            logger.warning("AP242 XML: skipping nested %s (cycle/depth)", file_ref.name)
            return PartNode(
                name=name,
                product_id=name,
                ref_key=f"unexpanded:{ctx.file_key(file_ref)}",
                transform=transform,
            )
        return ctx.build_file(str(file_ref), transform, name, depth + 1, visiting)

    def _leaf_with_geometry(self, file_ref, name, transform, ctx) -> PartNode:
        # ref_key keyed on the geometry file so the same part reused anywhere in a
        # (possibly nested) assembly collapses to one AAS (D2).
        node = PartNode(
            name=name,
            product_id=file_ref.name,
            ref_key=ctx.file_key(file_ref),
            transform=transform,
            source_file=str(file_ref),
        )
        try:
            shape, props, bbox, pmi = ctx.load_geometry(str(file_ref))
            node.shape_ref, node.props, node.bbox_mm, node.has_pmi = shape, props, bbox, pmi
        except Exception as exc:  # noqa: BLE001
            logger.warning("AP242 XML: failed to load %s (%s)", file_ref.name, exc)
            node.props = PhysicalProps(provenance=Provenance(source="fallback"))
        return node

    # --- element helpers ----------------------------------------------------

    def _resolve_part_file(self, info: dict, ctx: _Context) -> pathlib.Path | None:
        for pv_uid in info["pv_uids"]:
            pv = self.by_uid.get(pv_uid)
            if pv is None:
                continue
            for da in pv.findall(".//{*}DocumentAssignment"):
                ad = da.find("{*}AssignedDocument")
                if ad is None:
                    continue
                target = self.by_uid.get(ad.attrib.get("uidRef"))
                for file_el in self._file_elements(target):
                    for cand in self._file_name_candidates(file_el):
                        candidate = pathlib.Path(cand.replace("\\", "/"))
                        if candidate.suffix.lower() not in _XML_EXT | _STEP_EXT:
                            continue
                        found = self._find_file(cand)
                        dependency = found if found is not None else self.dir / candidate
                        role = "xml" if candidate.suffix.lower() in _XML_EXT else "geometry"
                        # D5: missing selected candidates are explicit, not silently excluded.
                        ctx.record_dependency(dependency, role)
                        if found is not None:
                            return found
        return None

    def _file_elements(self, target) -> list:
        """Actual <File> elements reachable from an AssignedDocument target.

        The target may be a File directly (km3/r50j) or, in the full AP242 document
        model (RackandPinion/Screw/km1), a Document/DocumentVersion that reaches the
        File via DocumentDefinition → Files → DigitalFile(uidRef). We collect Files
        both nested and via uidRef references, at any depth.
        """
        if target is None:
            return []
        out: list = []
        seen: set[int] = set()

        def add(el) -> None:
            if el is not None and _ln(el) == "File" and id(el) not in seen:
                seen.add(id(el))
                out.append(el)

        add(target)
        for el in target.iter():
            add(el)
            ref = el.attrib.get("uidRef")
            if ref and _ln(el) in ("DigitalFile", "File"):
                add(self.by_uid.get(ref))
        return out

    @staticmethod
    def _file_name_candidates(file_el) -> list[str]:
        """A File element names its target in several dialect-specific places."""
        names: list[str] = []
        ext = file_el.find(".//{*}ExternalItem/{*}Id")  # km3 dialect
        if ext is not None and ext.attrib.get("id"):
            names.append(ext.attrib["id"])
        for src in file_el.findall(".//{*}FileLocationIdentification/{*}SourceId"):  # allinone
            if src.text:
                names.append(src.text.strip())
        ident = file_el.find(".//{*}Id/{*}Identifier")  # common fallback
        if ident is not None and ident.attrib.get("id"):
            names.append(ident.attrib["id"])
        # de-dup, keep order
        seen, out = set(), []
        for n in names:
            if n and n not in seen:
                seen.add(n)
                out.append(n)
        return out

    def _find_file(self, name: str) -> pathlib.Path | None:
        if not name:
            return None
        # S6/D2: preserve directory components, including Windows-style separators.
        path = pathlib.Path(name.replace("\\", "/"))
        exact = self.dir / path
        if exact.is_file():
            return exact.resolve()
        # Some exporters vary filename case. Match each component within its real
        # parent; never substitute an unrelated same-basename file beside the XML.
        cursor = pathlib.Path(exact.anchor)
        for component in exact.parts[1:]:
            direct = cursor / component
            if direct.exists() or component in (".", ".."):
                cursor = direct
                continue
            if not cursor.is_dir():
                return None
            matches = [
                entry for entry in cursor.iterdir() if entry.name.casefold() == component.casefold()
            ]
            if len(matches) > 1:
                raise ValueError(f"AP242 XML {self.path}: ambiguous file reference {name!r}")
            if not matches:
                return None
            cursor = matches[0]
        return cursor.resolve() if cursor.is_file() else None

    def _transform(self, vor) -> list[list[float]] | None:
        ct = vor.find(".//{*}CartesianTransformation")
        if ct is None:
            return None
        rot = ct.find("{*}RotationMatrix")
        trans = ct.find("{*}TranslationVector")
        r = _floats(rot.text) if rot is not None else []
        t = _floats(trans.text) if trans is not None else []
        if len(r) < 9 or len(t) < 3:
            return None
        # AP242 RotationMatrix lists the target X/Y/Z axis vectors as COLUMNS
        # (xx xy xz  yx yy yz  zx zy zz) — column-major. Transpose to row-major so
        # p' = M·p places the part correctly (cf. STP2X3D AP242XML_Reader SetValues).
        return [
            [r[0], r[3], r[6], t[0]],
            [r[1], r[4], r[7], t[1]],
            [r[2], r[5], r[8], t[2]],
            [0.0, 0.0, 0.0, 1.0],
        ]

    def _occ_name(self, occ_uid: str) -> str | None:
        occ = self.by_uid.get(occ_uid)
        if occ is None:
            return None
        id_el = occ.find("{*}Id")
        return id_el.attrib.get("id") if id_el is not None else None

    def header(self) -> P21Header:
        h = self.root_el.find(".//{*}Header")
        if h is None:
            return P21Header(schema="AP242")

        def txt(path):
            el = h.find(path)
            return (el.text or "").strip() if el is not None and el.text else None

        return P21Header(
            schema="AP242",
            organization=(
                txt(".//{*}Organization/{*}Name/{*}CharacterString")
                or txt(".//{*}Organization/{*}Name")
            ),
            originating_system=txt("{*}OriginatingSystem"),
            timestamp=txt("{*}TimeStamp"),
        )


def _floats(text: str | None) -> list[float]:
    if not text:
        return []
    out = []
    for tok in text.replace(",", " ").split():
        try:
            out.append(float(tok))
        except ValueError:
            pass
    return out


def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()
