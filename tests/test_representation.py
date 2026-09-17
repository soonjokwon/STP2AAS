"""C22: actual OCCT topology determines the 02026 representation value."""

from __future__ import annotations

import pytest

pytest.importorskip("OCC")

from OCC.Core.BRep import BRep_Builder
from OCC.Core.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakeVertex,
)
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.gp import gp_Dir, gp_Pln, gp_Pnt
from OCC.Core.Poly import Poly_Triangle, Poly_Triangulation
from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Face

from step2aas.mapping.models3d import build_models3d
from step2aas.model import P21Header, PartNode


def _shapes():
    mesh = Poly_Triangulation(3, 1, False)
    for index, point in enumerate((gp_Pnt(), gp_Pnt(1, 0, 0), gp_Pnt(0, 1, 0)), 1):
        mesh.SetNode(index, point)
    mesh.SetTriangle(1, Poly_Triangle(1, 2, 3))
    mesh_face = TopoDS_Face()
    BRep_Builder().MakeFace(mesh_face, mesh)
    return {
        "SolidBody": BRepPrimAPI_MakeBox(2, 3, 4).Shape(),
        "Surface": BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(), gp_Dir(0, 0, 1)), 0, 2, 0, 3).Shape(),
        "WireFrame": BRepBuilderAPI_MakeEdge(gp_Pnt(), gp_Pnt(1, 2, 3)).Shape(),
        "PointCloud": BRepBuilderAPI_MakeVertex(gp_Pnt(1, 2, 3)).Shape(),
        "Mesh": mesh_face,
    }


def _geometry(part):
    sm = build_models3d(part, "model.stp", "preview.png", "urn:sm:test", P21Header())
    entry = next(iter(next(iter(sm.submodel_element)).value))
    return next((e for e in entry.value if e.id_short == "Geometry"), None)


def _representation(part):
    geometry = _geometry(part)
    return next((e for e in geometry.value if e.id_short == "Representation"), None)


@pytest.mark.parametrize("expected", ["SolidBody", "Surface", "WireFrame", "PointCloud", "Mesh"])
def test_representation_matches_actual_topology(expected):
    part = PartNode("Part", "part", shape_ref=_shapes()[expected])
    representation = _representation(part)
    assert representation.value == expected
    assert next(iter(representation.qualifier)).value == "computed"


def test_render_mesh_does_not_reclassify_analytic_surface():
    face = _shapes()["Surface"]
    BRepMesh_IncrementalMesh(face, 0.1).Perform()
    assert _representation(PartNode("Face", "face", shape_ref=face)).value == "Surface"


def _compound(*shapes):
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, shape)
    return compound


def test_homogeneous_compound_and_xml_assembly_are_classified():
    shapes = _shapes()
    compound = _compound(shapes["WireFrame"], shapes["WireFrame"])
    assert _representation(PartNode("Wires", "wires", shape_ref=compound)).value == "WireFrame"
    part = PartNode(
        "Assembly",
        "assembly",
        children=[
            PartNode("Leaf", "leaf", shape_ref=shapes["SolidBody"]),
            PartNode("Leaf2", "leaf2", shape_ref=shapes["SolidBody"]),
        ],
    )
    assert _representation(part).value == "SolidBody"


@pytest.mark.parametrize("case", ["missing", "mixed", "empty", "partial_assembly"])
def test_unknown_or_mixed_representation_omits_optional_geometry(case, caplog):
    shapes = _shapes()
    if case == "mixed":
        part = PartNode(
            "Mixed", "mixed", shape_ref=_compound(shapes["SolidBody"], shapes["WireFrame"])
        )
    elif case == "empty":
        part = PartNode("Empty", "empty", shape_ref=_compound())
    elif case == "partial_assembly":
        part = PartNode(
            "Partial",
            "partial",
            children=[
                PartNode("Known", "known", shape_ref=shapes["Surface"]),
                PartNode("Missing", "missing"),
            ],
        )
    else:
        part = PartNode("Missing", "missing")
    assert _geometry(part) is None
    assert "omitting Geometry" in caplog.text


def test_missing_topology_rules_do_not_reuse_legacy_solid_default(monkeypatch):
    from step2aas.mapping import models3d

    monkeypatch.delitem(models3d._CONST, "RepresentationKinds")
    part = PartNode("Wire", "wire", shape_ref=_shapes()["WireFrame"])
    with pytest.raises(KeyError, match="RepresentationKinds"):
        _geometry(part)


@pytest.mark.parametrize("kind", ["solid", "wire"])
def test_non_null_empty_containers_do_not_invent_a_representation(kind):
    from OCC.Core.TopoDS import TopoDS_Solid, TopoDS_Wire

    builder = BRep_Builder()
    if kind == "solid":
        shape = TopoDS_Solid()
        builder.MakeSolid(shape)
    else:
        shape = TopoDS_Wire()
        builder.MakeWire(shape)
    assert not shape.IsNull()
    assert _geometry(PartNode("Empty", "empty", shape_ref=shape)) is None


def test_triangulation_only_solid_preserves_mesh_representation():
    from OCC.Core.TopoDS import TopoDS_Shell, TopoDS_Solid

    builder = BRep_Builder()
    shell = TopoDS_Shell()
    builder.MakeShell(shell)
    builder.Add(shell, _shapes()["Mesh"])
    solid = TopoDS_Solid()
    builder.MakeSolid(solid)
    builder.Add(solid, shell)
    for shape in (shell, solid):
        assert _representation(PartNode("Mesh", "mesh", shape_ref=shape)).value == "Mesh"
