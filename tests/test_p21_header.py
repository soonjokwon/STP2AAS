"""P21 header parser — no pythonocc required."""

from __future__ import annotations

from step2aas.extract.p21_header import parse_header


def test_parse_header_ap242(tmp_path):
    p = tmp_path / "part.stp"
    p.write_text(
        """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('step2aas test'),'2;1');
FILE_NAME('part.stp','2024-06-01T12:00:00',('Ada'),('TestOrg'),
  'preprocessor','NX 2206','auth');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));
ENDSEC;
DATA;
ENDSEC;
END-ISO-10303-21;
""",
        encoding="ascii",
    )
    h = parse_header(str(p))
    assert h.schema == "AP214"
    assert h.organization == "TestOrg"
    assert h.originating_system == "NX 2206"
    assert h.timestamp == "2024-06-01T12:00:00"
    assert h.author == "Ada"


def test_parse_header_multiline_and_ap242(tmp_path):
    p = tmp_path / "a.stp"
    p.write_text(
        """ISO-10303-21;
HEADER;
FILE_NAME(
  'x.stp',
  '2020-01-02T00:00:00',
  ('a'),
  ('Org'),
  'pre',
  'Catia V5',
  ' ');
FILE_SCHEMA(('MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF { 1 0 10303 442 1 1 4 }'));
ENDSEC;
DATA;
ENDSEC;
END-ISO-10303-21;
""",
        encoding="ascii",
    )
    h = parse_header(str(p))
    assert h.schema == "AP242"
    assert h.originating_system == "Catia V5"
