# STEP to Twin (stp2aas)

STEP (ISO 10303) CAD models → Asset Administration Shell (AAS) digital twin packages (.aasx).

Implementation for the paper *"STEP to Twin: Generating Asset Administration Shell (AAS)
Digital Twins from STEP Models"*.

## Setup (self-contained — everything in this one folder)

`pythonocc-core` is not pip-installable, so the runtime is a conda env. Running
**`setup.bat`** builds that env **inside this folder at `.\env`** (installing
Miniforge via winget first if conda is missing). Code + environment then live
together in one folder; the `.bat` launchers call `.\env\python.exe` directly with
`PYTHONPATH=src`, so nothing is hardcoded and no `conda activate` is needed.

```
setup.bat                                 :: one-time; builds .\env from environment.yml
run_gui.bat                               :: launch the desktop GUI (pick a file, convert, view)
run.bat  input.stp -o out\model.aasx      :: CLI
view.bat out\model.aasx --open            :: build + open the 3D viewer
```

Moving to another PC: copy the folder and run `setup.bat` once (it rebuilds `.\env`;
needs internet). `.\env` is git-ignored and not committed. (For a zero-install copy
that runs without rebuilding, the env can be made relocatable with `conda-pack` —
ask if you want that.)

## GUI

`run_gui.bat` (or `python scripts/gui.py`) opens a window to choose an input
STEP/AP242 file, convert it to `.aasx`, and open the interactive 3D viewer — the
same pipeline as the CLI, no commands needed.

## Usage (CLI)

STP2X3D-style flags (`--input`/`--output`; short `-i`/`-o`). `--output` is optional
and defaults to `<input>.aasx` beside the input. Bare positional forms also work.

```
run.bat --input <file> [--output <file.aasx>] [options]
python -m stp2aas -i <file> [-o <file.aasx>] [options]
```

Inputs:
- **Part 21 STEP** (`.stp` / `.step`) — AP203 / AP214 / AP242, single part or assembly.
- **AP242 Domain Model XML** (`.stpx` / `.xml`) — CAx-IF/MBx-IF Assembly Structure BOM,
  both all-in-one and nested (multi-file sub-assembly) layouts. Referenced external
  STEP part files are resolved relative to the XML.

Options:
- `--assembly-structure {hierarchical,flat,single}` — AAS composition (default `hierarchical`):
  - `hierarchical` — one AAS per unique part **and** per unique sub-assembly; each
    assembly's 02011 BOM lists its direct children (`ArcheType=OneDown`) and
    `SameAs`-links them to their own AAS (standards-faithful).
  - `flat` — one assembly AAS (root) + one AAS per unique part; the root BOM nests the
    whole tree (`ArcheType=Full`), nested sub-assemblies appear as co-managed BOM nodes.
  - `single` — one AAS for the whole model (all parts as Model3D entries + a co-managed
    `Full` BOM tree); experimental comparison baseline.

  The root `globalAssetId` is identical across modes (same physical asset), while root
  AAS/submodel ids differ per mode — outputs of several modes for the same file can be
  loaded into one repository side by side. Part AAS are shared between hierarchical/flat.
- `--link-only` — reference the source file externally instead of embedding (whole-model).
- `--include-partial-nameplate` — emit the intentionally-incomplete 02006 Nameplate
  (default: omit, per policy D7).
- `--view` — also build the HTML 3D viewer next to the `.aasx`.
- `--open` — open that viewer in a browser (implies `--view`).
- `--no-3d` — with `--view`, build the viewer without 3D geometry (structure only; faster).
- `-v/--verbose` — verbose logging (shows gap/fallback decisions).

Examples:
```
run.bat --input tests\fixtures\single_part.stp                       # → single_part.aasx
run.bat --input assembly.stp --output out\assembly.aasx --view       # + assembly.html
run.bat --input "Torque_Convertor_Assy.stpx" --open                  # convert + open viewer
```

## What it produces

- One AAS per **unique part** (TechnicalData §3 + Models3D §4) and, for assemblies, one
  **assembly AAS** carrying the 02011 BOM (§2) whose Nodes `SameAs` the part AAS assets.
- Repeated parts collapse to a single AAS referenced by multiple BOM Nodes (design decision D2).
- The original STEP is embedded per part as an AASX supplementary file; a 512² preview PNG is
  rendered offscreen (solid-colour placeholder fallback on headless GL failure, D6).

## Visualization

Render an `.aasx` to a self-contained HTML viewer — AAS structure tree + BOM +
TechnicalData + preview thumbnails on one side, and an **interactive WebGL 3D
viewport** (geometry tessellated from the embedded STEP with pythonocc) on the
other. No server, no third-party JS; open the `.html` in any browser.

Build it during conversion (`run.bat --input model.stp --view`), or from an existing
`.aasx`:

```
view.bat out\suspension.aasx --open       # build + open (click a part to load its 3D)
view.bat out\part.aasx --no-3d            # structure only (faster)
python -m stp2aas.viewer out\x.aasx      # equivalent module form
```

Toolbar: Iso/Front/Top/Right view presets, Fit, Wire, section Clip (X/Y/Z + slider),
and an opacity slider. Solids render shaded; zero-thickness sheet bodies (datum/PMI
planes) render as edge outlines.

## Testing

```
pytest -x -q
```

Roundtrip tests convert fixtures, reload the `.aasx` with basyx, and assert submodels,
semanticIds, and required cardinalities. Drop STEP/AP242 datasets under `tests/` (gitignored)
to exercise real-world models; AP242 tests skip when none are present.

## Docs
- `docs/mapping-draft.md` — STEP ↔ IDTA submodel mapping specification (authoritative)
- `docs/verification-log.md` — semanticId verification status + extraction/AP242 validation
- `docs/milestones.md` — development plan
