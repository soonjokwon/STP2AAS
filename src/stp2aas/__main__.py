"""CLI entry (STP2X3D-style flags): python -m stp2aas --input <file> [--output <file>]"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys


def _make_console_unicode_safe() -> None:
    """Avoid UnicodeEncodeError when printing non-ASCII paths on legacy consoles
    (e.g. Korean cp949): reconfigure stdout/stderr to UTF-8, replacing on error."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — best effort; not all streams support it
            pass


def main(argv: list[str] | None = None) -> int:
    _make_console_unicode_safe()
    p = argparse.ArgumentParser(
        prog="stp2aas",
        add_help=False,  # no -h/--help; running with no arguments prints this help
        description=(
            "Convert a STEP model (AP203/AP214/AP242) or an AP242 Domain Model / BOM "
            "XML (.stpx) into an AAS digital-twin package (.aasx)."
        ),
        usage="stp2aas --input <file> [--output <file.aasx>] [options]",
        epilog=(
            "examples (via run.bat):\n"
            "  run.bat --input model.stp\n"
            "  run.bat --input model.stp --output out\\model.aasx\n"
            '  run.bat --input "assembly.stpx" --output out\\asm.aasx   (AP242 BOM XML)\n'
            "\nnotes:\n"
            "  --output is optional; it defaults to <input>.aasx beside the input.\n"
            "  positional forms also work:  run.bat model.stp out\\model.aasx\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # STP2X3D-style flags: --input / --output (short -i/-o). Positional forms are
    # also accepted as a convenience.
    p.add_argument("-i", "--input", dest="input", help="input CAD model (.stp/.step/.stpx/.xml)")
    p.add_argument(
        "-o",
        "--output",
        dest="output",
        help="output .aasx (default: <input>.aasx beside the input)",
    )
    p.add_argument("pos_input", nargs="?", default=None, help=argparse.SUPPRESS)
    p.add_argument("pos_output", nargs="?", default=None, help=argparse.SUPPRESS)
    p.add_argument(
        "--link-only",
        action="store_true",
        help="reference files externally instead of embedding (02026 ExternalFile)",
    )
    p.add_argument(
        "--include-partial-nameplate",
        action="store_true",
        help="emit the intentionally-incomplete 02006 Nameplate (default: omit, D7)",
    )
    p.add_argument(
        "--assembly-structure",
        dest="assembly_structure",
        choices=("hierarchical", "flat", "single"),
        default="hierarchical",
        help="AAS composition: hierarchical (default) | flat | single",
    )
    p.add_argument(
        "--view", action="store_true", help="also build an HTML 3D viewer beside the .aasx"
    )
    p.add_argument(
        "--open", action="store_true", help="open the HTML viewer in a browser (implies --view)"
    )
    p.add_argument(
        "--no-3d",
        action="store_true",
        help="viewer without 3D geometry (structure only; needs --view)",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    input_path = args.input or args.pos_input
    if not input_path:
        p.print_help()  # no input → show full help (same as -h)
        return 0

    from stp2aas.aasx_writer import write_aasx

    output = args.output or args.pos_output or str(pathlib.Path(input_path).with_suffix(".aasx"))
    doc = _extract(input_path)
    write_aasx(
        doc,
        output,
        embed=not args.link_only,
        include_partial_nameplate=args.include_partial_nameplate,
        assembly_structure=args.assembly_structure,
    )
    print(f"wrote {output}")

    if args.view or args.open:
        from stp2aas import viewer

        html, n = viewer.render(output, enable_3d=not args.no_3d, open_browser=args.open)
        print(f"wrote {html}  ({n} 3D meshes)")
    return 0


def _extract(input_path: str):
    """Dispatch on input type: AP242 Domain Model XML (.stpx/.xml) vs Part 21 STEP."""
    from stp2aas.extract import extract_any

    return extract_any(input_path)


if __name__ == "__main__":
    sys.exit(main())
