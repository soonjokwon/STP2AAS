"""Verify generated packages with the independent aas-core3.1 SDK (AASd constraints).

Usage: python aascore_verify.py <zip_or_aasx_dir> <out.json>
Each archive's data.xml is parsed with aas_core3_1.xmlization and checked with
aas_core3_1.verification.verify, which implements the metamodel constraints.
"""
from __future__ import annotations

import collections
import glob
import io
import json
import re
import sys
import zipfile

import aas_core3_1.verification as verification
import aas_core3_1.xmlization as xmlization


def check(path: str) -> dict:
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.endswith("data.xml") or n.endswith(".aas.xml"))
        data = z.read(name)
    try:
        env = xmlization.environment_from_stream(io.BytesIO(data))
    except Exception as exc:  # deserialization error
        return {"file": path, "parsed": False, "error": str(exc)[:300], "errors": []}
    errs = [f"{e.path}: {e.cause}" for e in verification.verify(env)]
    return {"file": path, "parsed": True, "errors": errs}


def main(src: str, out: str) -> None:
    files = sorted(glob.glob(f"{src}/*.zip") + glob.glob(f"{src}/*.aasx"))
    res = [check(f) for f in files]
    kinds = collections.Counter()
    for r in res:
        for e in r["errors"]:
            kinds[re.sub(r"\[\d+\]", "[i]", e.split(": ", 1)[1] if ": " in e else e)[:160]] += 1
    summary = {"packages": len(res), "parsed": sum(r["parsed"] for r in res),
               "packages_with_errors": sum(1 for r in res if r["errors"] or not r["parsed"]),
               "error_kinds": kinds.most_common(20)}
    json.dump({"summary": summary, "results": [r for r in res if r["errors"] or not r["parsed"]][:50]},
              open(out, "w"), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:3])
