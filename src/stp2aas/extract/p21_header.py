"""S11: parse the Part 21 HEADER section with plain text handling (no OCCT).

See mapping doc §1 S11 and CLAUDE.md gotchas. FILE_SCHEMA drives AP203/214/242
detection (C8). This module has no pythonocc/basyx dependency on purpose so it
can be exercised in isolation.
"""

from __future__ import annotations

import re

from stp2aas.model import P21Header

# ISO 10303-21 permits arbitrary whitespace (incl. newlines) between tokens, so
# we never assume a header keyword and its argument list share a line.
_HEADER_RE = re.compile(r"HEADER\s*;(?P<body>.*?)ENDSEC\s*;", re.IGNORECASE | re.DOTALL)


def parse_header(path: str) -> P21Header:
    """Read FILE_NAME / FILE_SCHEMA from the HEADER section (S11, C8).

    Tolerates line breaks inside entities and strips P21 string quoting. Maps the
    FILE_SCHEMA identifier to a normalized AP name (AP203/AP214/AP242).
    """
    text = _read_until_endsec(path)
    header = _extract_header_block(text)

    fn_args = _split_top_level(_entity_args(header, "FILE_NAME"))
    fs_args = _split_top_level(_entity_args(header, "FILE_SCHEMA"))

    # FILE_NAME(name, time_stamp, (author...), (organization...),
    #           preprocessor_version, originating_system, authorisation)
    name = _p21_string(fn_args[0]) if len(fn_args) > 0 else None
    timestamp = _p21_string(fn_args[1]) if len(fn_args) > 1 else None
    author = _first_in_list(fn_args[2]) if len(fn_args) > 2 else None
    organization = _first_in_list(fn_args[3]) if len(fn_args) > 3 else None
    originating_system = _p21_string(fn_args[5]) if len(fn_args) > 5 else None
    _ = name  # FILE_NAME.name is the STEP filename, not needed downstream

    schema_id = _first_in_list(fs_args[0]) if fs_args else None

    return P21Header(
        schema=_normalize_schema(schema_id),
        author=author or None,
        organization=organization or None,
        originating_system=originating_system or None,
        timestamp=timestamp or None,
    )


def _read_until_endsec(path: str, max_bytes: int = 1 << 20) -> str:
    """Read enough of the file to cover the HEADER; stop early once ENDSEC seen."""
    chunks: list[str] = []
    read = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            chunks.append(line)
            read += len(line)
            # The first ENDSEC always closes the HEADER (DATA section follows).
            if "ENDSEC" in line.upper() or read >= max_bytes:
                break
    return "".join(chunks)


def _extract_header_block(text: str) -> str:
    m = _HEADER_RE.search(text)
    if not m:
        raise ValueError("No HEADER;...ENDSEC; block found — not a valid Part 21 file")
    return m.group("body")


def _entity_args(header: str, keyword: str) -> str:
    """Return the raw argument string inside ``keyword( ... )`` with balanced parens."""
    m = re.search(rf"\b{keyword}\s*\(", header, re.IGNORECASE)
    if not m:
        return ""
    start = m.end()  # position just after the opening '('
    depth = 1
    in_str = False
    i = start
    while i < len(header):
        c = header[i]
        if in_str:
            if c == "'":
                # Doubled '' is an escaped quote, not a terminator.
                if i + 1 < len(header) and header[i + 1] == "'":
                    i += 2
                    continue
                in_str = False
        else:
            if c == "'":
                in_str = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return header[start:i]
        i += 1
    return header[start:]


def _split_top_level(args: str) -> list[str]:
    """Split a P21 argument string on top-level commas (ignoring strings/nesting)."""
    out: list[str] = []
    depth = 0
    in_str = False
    cur: list[str] = []
    i = 0
    while i < len(args):
        c = args[i]
        if in_str:
            cur.append(c)
            if c == "'":
                if i + 1 < len(args) and args[i + 1] == "'":
                    cur.append(args[i + 1])
                    i += 2
                    continue
                in_str = False
        else:
            if c == "'":
                in_str = True
                cur.append(c)
            elif c == "(":
                depth += 1
                cur.append(c)
            elif c == ")":
                depth -= 1
                cur.append(c)
            elif c == "," and depth == 0:
                out.append("".join(cur).strip())
                cur = []
            else:
                cur.append(c)
        i += 1
    if cur:
        out.append("".join(cur).strip())
    return out


def _first_in_list(arg: str) -> str | None:
    """A P21 list arg like ``('a','b')`` → first decoded string, or None if empty/unset."""
    arg = arg.strip()
    if not arg or arg in ("$", "*"):
        return None
    if arg.startswith("(") and arg.endswith(")"):
        inner = _split_top_level(arg[1:-1])
        for item in inner:
            s = _p21_string(item)
            if s:
                return s
        return None
    return _p21_string(arg)


def _p21_string(token: str) -> str | None:
    """Decode a single P21 token to a Python string (None for unset ``$``/``*``)."""
    token = token.strip()
    if not token or token in ("$", "*"):
        return None
    if token.startswith("'") and token.endswith("'") and len(token) >= 2:
        body = token[1:-1].replace("''", "'")
        return _decode_control_directives(body).strip() or None
    return token or None


def _decode_control_directives(s: str) -> str:
    r"""Best-effort decode of P21 control directives (\X2\....\X0\, \X\HH, \S\c)."""

    def _x2(m: re.Match[str]) -> str:
        hexs = re.findall(r"[0-9A-Fa-f]{4}", m.group(1))
        return "".join(chr(int(h, 16)) for h in hexs)

    s = re.sub(r"\\X2\\([0-9A-Fa-f]+)\\X0\\", _x2, s)
    s = re.sub(r"\\X\\([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r"\\S\\(.)", lambda m: m.group(1), s)  # ISO 8859 shift — approximate
    return s


def _normalize_schema(schema_id: str | None) -> str | None:
    """Map a FILE_SCHEMA identifier to a normalized AP name (C8).

    The schema name itself is the reliable signal: AP242 schemas literally contain
    "AP242"; AP214 uses AUTOMOTIVE_DESIGN; AP203 uses CONFIG_CONTROL_DESIGN. The
    bracketed number (e.g. ``214``, ``442``) is a secondary hint only.
    """
    if not schema_id:
        return None
    s = schema_id.upper()
    if "AP242" in s or "MANAGED_MODEL_BASED_3D_ENGINEERING" in s:
        return "AP242"
    if "AUTOMOTIVE_DESIGN" in s:
        return "AP214"
    if "CONFIG_CONTROL_DESIGN" in s:
        return "AP203"
    # Fall back to the object-identifier number inside the braces.
    m = re.search(r"10303\s+(\d{3})", s)
    if m:
        return f"AP{m.group(1)}"
    m = re.search(r"\b(203|214|242)\b", s)
    if m:
        return f"AP{m.group(1)}"
    return schema_id.strip() or None
