"""Generate small synthetic STEP fixtures with pythonocc (XDE) for development.

Not a substitute for NIST CTC / MBE PMI validation models. These are minimal,
self-authored geometries so the pipeline and roundtrip tests can run without
downloading arbitrary STEP files:

  single_part.stp  — one simple solid (M1)
  assembly.stp     — assembly of 2 distinct prototypes; one reused twice at
                     different placements (exercises BOM + duplicate handling D2)

Run:  python scripts/make_test_fixtures.py
"""

from __future__ import annotations

import pathlib

# Importing OCC.Extend.DataExchange registers the XCAF storage drivers; without
# it, constructing a TDocStd_Document crashes the interpreter (pythonocc 7.9).
import OCC.Extend.DataExchange  # noqa: F401
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCC.Core.gp import gp_Trsf, gp_Vec
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.STEPCAFControl import STEPCAFControl_Writer
from OCC.Core.STEPControl import STEPControl_AsIs
from OCC.Core.TDataStd import TDataStd_Name
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def _new_doc() -> TDocStd_Document:
    return TDocStd_Document("XmlXCAF")


def _set_name(label, name: str) -> None:
    # pythonocc 7.9 expects a plain Python str (SWIG converts to ExtendedString).
    TDataStd_Name.Set(label, name)


def _write(doc: TDocStd_Document, path: pathlib.Path) -> None:
    writer = STEPCAFControl_Writer()
    writer.Transfer(doc, STEPControl_AsIs)
    status = writer.Write(str(path))
    if status != IFSelect_RetDone:
        raise RuntimeError(f"STEP write failed for {path}")
    print(f"wrote {path}")


def _translation(dx: float, dy: float, dz: float) -> TopLoc_Location:
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(dx, dy, dz))
    return TopLoc_Location(trsf)


def make_single_part(path: pathlib.Path) -> None:
    doc = _new_doc()
    st = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    box = BRepPrimAPI_MakeBox(40.0, 30.0, 20.0).Shape()
    lab = st.AddShape(box, False)  # makeAssembly=False -> simple shape
    _set_name(lab, "Bracket")
    _write(doc, path)


def make_assembly(path: pathlib.Path) -> None:
    doc = _new_doc()
    st = XCAFDoc_DocumentTool.ShapeTool(doc.Main())

    # Two distinct prototype parts (added as non-assembly simple shapes).
    box = BRepPrimAPI_MakeBox(20.0, 20.0, 20.0).Shape()
    box_lab = st.AddShape(box, False)
    _set_name(box_lab, "Cube")

    cyl = BRepPrimAPI_MakeCylinder(5.0, 30.0).Shape()
    cyl_lab = st.AddShape(cyl, False)
    _set_name(cyl_lab, "Pin")

    # Empty assembly container, then place components (references + locations).
    asm_lab = st.NewShape()
    _set_name(asm_lab, "Widget")
    st.AddComponent(asm_lab, box_lab, _translation(0, 0, 0))
    st.AddComponent(asm_lab, cyl_lab, _translation(50, 0, 0))
    # Reuse the Cube prototype a second time -> D2 (same part AAS, two Nodes).
    st.AddComponent(asm_lab, box_lab, _translation(0, 50, 0))
    st.UpdateAssemblies()
    _write(doc, path)


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    make_single_part(FIXTURES / "single_part.stp")
    make_assembly(FIXTURES / "assembly.stp")


if __name__ == "__main__":
    main()
