# pythonocc-core compatibility layer over OCP

Used where the conda-forge `pythonocc-core` package is unavailable. It maps
`OCC.Core.<Module>` imports onto `OCP.<Module>` from the `cadquery-ocp` wheel
(pin the wheel to the same OCCT release, e.g. `cadquery-ocp==7.9.3.1`).

    pip install cadquery-ocp==7.9.3.1 basyx-python-sdk==2.1.0 pyyaml pillow lxml pytest
    PYTHONPATH=scripts/ocp_compat:src python -m pytest -q

Bridged differences: OCP static methods `X_s` are exposed as `X`; lowercase
helper classes (`topods`, `brepgprop`, ...) resolve case-insensitively; `str`
arguments are retried as `TCollection_ExtendedString`/`TCollection_AsciiString`;
`TDF_Label.GetLabelName` is provided. The offscreen viewer is not available, so
previews fall back to the placeholder image (D6). All tests pass under the layer.
