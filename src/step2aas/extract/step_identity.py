"""S4 / gap G2: what the STEP file itself says about the identity of each design
and each use, mapped onto the XDE labels the rest of the extraction works with.

For every prototype label (a design) this gives product.id, product.name, the
product_definition_formation.id (the version) and the product_definition's
instance name in the source file. For every component label (one use) it gives
the next_assembly_usage_occurrence's id attribute and instance name. Files that
follow the CAx-IF recommended practice "Persistent IDs" (AP242 Edition 4) also
assign UUIDs to these entities; they are picked up when present.

Everything here is optional evidence. Any failure leaves the fields empty and
the conversion continues with file-scoped identifiers.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class DesignIdentity:
    product_id: str | None = None
    product_name: str | None = None
    version_id: str | None = None
    pd_ref: str | None = None  # "#39" in the source file
    uuid: str | None = None


@dataclass
class UseIdentity:
    nauo_id: str | None = None
    nauo_ref: str | None = None  # "#412" in the source file
    uuid: str | None = None


@dataclass
class StepIdentity:
    designs: dict[str, DesignIdentity] = field(default_factory=dict)  # prototype entry →
    uses: dict[str, UseIdentity] = field(default_factory=dict)  # component entry →


# --- Part 21 text scan -------------------------------------------------------
# Open CASCADE does not expose instance names reliably through every Python
# binding, so they are read from the text. Entities appear in the model in file
# order; the k-th match here is the k-th entity of that type in the model, and
# the id attribute is compared as a check before any instance name is used.

_P21_STRING = r"'((?:[^']|'')*)'"
_RE_PD = re.compile(
    rb"#(\d+)\s*=\s*PRODUCT_DEFINITION(?:_WITH_ASSOCIATED_DOCUMENTS)?\s*\(\s*" + _P21_STRING.encode()
)
_RE_NAUO = re.compile(rb"#(\d+)\s*=\s*NEXT_ASSEMBLY_USAGE_OCCURRENCE\s*\(\s*" + _P21_STRING.encode())
_RE_PDS = re.compile(
    rb"#(\d+)\s*=\s*PRODUCT_DEFINITION_SHAPE\s*\(\s*(?:'(?:[^']|'')*'|\$)\s*,"
    rb"\s*(?:'(?:[^']|'')*'|\$)\s*,\s*#(\d+)\s*\)"
)
_RE_UUID_ATTR = re.compile(rb"#\d+\s*=\s*(?:HASH_BASED_V5_|V4_|V5_)?UUID_ATTRIBUTE\s*\(([^;]*)\)\s*;")
_RE_UUID = re.compile(
    rb"'([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})'"
)
_RE_REF = re.compile(rb"#(\d+)")


@dataclass
class _TextScan:
    pds: list[tuple[str, str]]  # (instance name, id attribute) in file order
    nauos: list[tuple[str, str]]
    uuid_of: dict[str, str]  # instance name → UUID (PDS UUIDs folded onto their PD)


def scan_p21(path: str) -> _TextScan:
    with open(path, "rb") as fh:
        data = fh.read()

    def _hits(rx):
        return [(f"#{m.group(1).decode()}", m.group(2).decode("latin-1")) for m in rx.finditer(data)]

    uuid_of: dict[str, str] = {}
    if b"UUID_ATTRIBUTE" in data:
        for m in _RE_UUID_ATTR.finditer(data):
            body = m.group(1)
            um = _RE_UUID.search(body)
            if not um:
                continue
            for ref in _RE_REF.findall(body):
                uuid_of.setdefault(f"#{ref.decode()}", um.group(1).decode().lower())
        if uuid_of:
            for m in _RE_PDS.finditer(data):
                pds_ref, pd_ref = f"#{m.group(1).decode()}", f"#{m.group(2).decode()}"
                if pds_ref in uuid_of:
                    uuid_of.setdefault(pd_ref, uuid_of[pds_ref])
    return _TextScan(pds=_hits(_RE_PD), nauos=_hits(_RE_NAUO), uuid_of=uuid_of)


# --- Open CASCADE side -------------------------------------------------------


def _cast(cls, ent):
    """The entity as `cls`, or None. OCP downcasts on return; pythonocc needs DownCast."""
    if isinstance(ent, cls):
        return ent
    down = getattr(cls, "DownCast", None)
    if down is None:
        return None
    try:
        out = down(ent)
    except Exception:  # noqa: BLE001
        return None
    if out is None or (hasattr(out, "IsNull") and out.IsNull()):
        return None
    return out


def _text(handle) -> str | None:
    """A Handle(TCollection_HAsciiString) as str (None when null or empty)."""
    try:
        if handle is None or (hasattr(handle, "IsNull") and handle.IsNull()):
            return None
        value = handle.ToCString()
    except Exception:  # noqa: BLE001
        return None
    return value or None


def _refs(scanned: list[tuple[str, str]], ids: list[str | None], what: str) -> list[str | None]:
    """Instance names for the model's entities, or all None when the text scan
    and the model disagree (complex instances, exotic formatting)."""
    if len(scanned) != len(ids):
        logger.info("STEP identity: %s count differs (text %d, model %d); no instance names",
                    what, len(scanned), len(ids))
        return [None] * len(ids)
    for (_, text_id), model_id in zip(scanned, ids):
        if text_id.isascii() and (model_id or "") != text_id:
            logger.info("STEP identity: %s order differs; no instance names", what)
            return [None] * len(ids)
    return [ref for ref, _ in scanned]


def collect(reader, shape_tool, path: str, entry_of) -> StepIdentity:
    """Map prototype and component labels to the STEP entities they came from.

    `reader` is the STEPCAFControl_Reader after Transfer, `entry_of(label)` the
    label's entry string. Never raises.
    """
    try:
        return _collect(reader, shape_tool, path, entry_of)
    except Exception as exc:  # noqa: BLE001 - optional evidence, see module docstring
        logger.warning("STEP identity not available (%s); using file-scoped identifiers", exc)
        return StepIdentity()


def _collect(reader, shape_tool, path, entry_of) -> StepIdentity:
    from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
    from OCC.Core.STEPConstruct import STEPConstruct_Tool
    from OCC.Core.StepBasic import StepBasic_ProductDefinition
    from OCC.Core.StepRepr import StepRepr_NextAssemblyUsageOccurrence
    from OCC.Core.TDF import TDF_Label, TDF_LabelSequence

    ws = reader.Reader().WS()
    model = ws.Model()
    pds, nauos = [], []
    for i in range(1, model.NbEntities() + 1):
        ent = model.Value(i)
        nauo = _cast(StepRepr_NextAssemblyUsageOccurrence, ent)
        if nauo is not None:
            nauos.append(nauo)
            continue
        pd = _cast(StepBasic_ProductDefinition, ent)
        if pd is not None:
            pds.append(pd)

    scan = scan_p21(path)
    pd_refs = _refs(scan.pds, [_text(pd.Id()) for pd in pds], "product_definition")
    nauo_refs = _refs(scan.nauos, [_text(n.Id()) for n in nauos], "NAUO")

    def _design(pd, ref) -> DesignIdentity:
        formation = pd.Formation()
        product = formation.OfProduct()
        return DesignIdentity(
            product_id=_text(product.Id()),
            product_name=_text(product.Name()),
            version_id=_text(formation.Id()),
            pd_ref=ref,
            uuid=scan.uuid_of.get(ref) if ref else None,
        )

    out = StepIdentity()
    index_of = {id(pd): k for k, pd in enumerate(pds)}

    def _signature(pd) -> tuple:
        formation = pd.Formation()
        product = formation.OfProduct()
        return (_text(product.Id()), _text(product.Name()), _text(formation.Id()), _text(pd.Id()))

    by_signature: dict[tuple, list[int]] = {}
    for k, pd in enumerate(pds):
        by_signature.setdefault(_signature(pd), []).append(k)

    def _pd_key(pd) -> int | None:
        # Python wrappers of one entity are not always the same object. Compare
        # the wrapped entity, then fall back to an unambiguous attribute match.
        k = index_of.get(id(pd))
        if k is not None:
            return k
        for j, other in enumerate(pds):
            try:
                if other.IsSame(pd) if hasattr(other, "IsSame") else other == pd:
                    return j
            except Exception:  # noqa: BLE001
                continue
        hits = by_signature.get(_signature(pd), [])
        return hits[0] if len(hits) == 1 else None

    related: set[int] = set()
    if nauos:
        tool = STEPConstruct_Tool(ws)
        label_map = reader.GetShapeLabelMap()
        for nauo, ref in zip(nauos, nauo_refs):
            k = _pd_key(nauo.RelatedProductDefinition())
            if k is not None:
                related.add(k)
            comp = STEPCAFControl_Reader.FindInstance(nauo, shape_tool, tool, label_map)
            if comp is None or comp.IsNull():
                continue
            out.uses[entry_of(comp)] = UseIdentity(
                nauo_id=_text(nauo.Id()),
                nauo_ref=ref,
                uuid=scan.uuid_of.get(ref) if ref else None,
            )
            proto = TDF_Label()
            if k is not None and shape_tool.GetReferredShape(comp, proto):
                out.designs.setdefault(entry_of(proto), _design(pds[k], pd_refs[k]))

    # The root design is the one product_definition that no NAUO uses.
    roots = TDF_LabelSequence()
    shape_tool.GetFreeShapes(roots)
    tops = [k for k in range(len(pds)) if k not in related]
    if roots.Length() == 1 and len(tops) == 1:
        out.designs.setdefault(entry_of(roots.Value(1)), _design(pds[tops[0]], pd_refs[tops[0]]))
    return out
