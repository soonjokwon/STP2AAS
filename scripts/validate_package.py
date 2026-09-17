"""Independent XML/package checks for the converter's generated profile.

No BaSyx or step2aas imports: XSD validation and selected semantic checks are
separate evidence. This is not a complete IDTA template certification tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from lxml import etree


def _validate_package(path: str | Path, xsd: str | Path | None = None) -> dict:
    errors = []
    with zipfile.ZipFile(path) as package:
        names = set(package.namelist())
        data = package.read("aasx/data.xml")
        root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))
        ns = {"a": etree.QName(root).namespace}

        def text(node, expression):
            return node.findtext(expression, namespaces=ns) or ""

        def find(node, expression):
            return node.findall(expression, namespaces=ns)

        def semantic(node):
            return text(node, "a:semanticId/a:keys/a:key/a:value")

        xsd_result = {"status": "not_run"}
        if xsd is not None:
            schema_bytes = Path(xsd).read_bytes()
            schema = etree.XMLSchema(etree.fromstring(schema_bytes))
            valid = schema.validate(root)
            xsd_result = {
                "status": "pass" if valid else "fail",
                "sha256": hashlib.sha256(schema_bytes).hexdigest(),
                "errors": [str(error) for error in schema.error_log],
            }
            if not valid:
                errors.append("XSD validation failed")
        shells = find(root, "a:assetAdministrationShells/a:assetAdministrationShell")
        submodels = find(root, "a:submodels/a:submodel")
        identifiables = shells + submodels
        by_id = {text(item, "a:id"): item for item in identifiables}
        if len(by_id) != len(identifiables) or "" in by_id:
            errors.append("missing or duplicate identifiable id")
        assets = {text(shell, "a:assetInformation/a:globalAssetId") for shell in shells}

        def resolve(reference):
            if reference is None or text(reference, "a:type") != "ModelReference":
                return None
            keys = find(reference, "a:keys/a:key")
            if not keys:
                return None
            def matches_key(key, target):
                if target is None:
                    return False
                local = etree.QName(target).localname
                return text(key, "a:type") == local[0].upper() + local[1:]

            item = by_id.get(text(keys[0], "a:value"))
            if not matches_key(keys[0], item):
                return None
            for key in keys[1:]:
                if item is None:
                    return None
                children = (
                    find(item, "a:submodelElements/*") + find(item, "a:statements/*")
                    + find(item, "a:value/*")
                )
                matches = [child for child in children
                           if text(child, "a:idShort") == text(key, "a:value")]
                item = matches[0] if len(matches) == 1 else None
                if not matches_key(key, item):
                    return None
            return item

        references = 0
        for shell in shells:
            for reference in find(shell, "a:submodels/a:reference"):
                references += 1
                target = resolve(reference)
                if target is None or etree.QName(target).localname != "submodel":
                    errors.append("unresolved shell submodel reference")

        entities = find(root, ".//a:entity")
        for entity in entities:
            kind = text(entity, "a:entityType")
            asset = text(entity, "a:globalAssetId")
            if kind == "SelfManagedEntity" and (not asset or asset not in assets):
                errors.append("self-managed entity asset has no shell in package")
            if kind == "CoManagedEntity" and asset:
                errors.append("co-managed entity carries globalAssetId")

        relationships = find(root, ".//a:relationshipElement")
        for relation in relationships:
            relation_semantic = semantic(relation)
            if not any(f"/{kind}/" in relation_semantic for kind in ("HasPart", "SameAs")):
                continue
            endpoints = [relation.find(f"a:{side}", ns) for side in ("first", "second")]
            targets = [resolve(endpoint) for endpoint in endpoints]
            references += 2
            if any(t is None or etree.QName(t).localname != "entity" for t in targets):
                errors.append("BOM relationship endpoints must resolve to Entity model references")
            if "/HasPart/" in relation_semantic:
                origins = [text(endpoint, "a:keys/a:key/a:value")
                           if endpoint is not None else "" for endpoint in endpoints]
                if origins[0] != origins[1]:
                    errors.append("HasPart crosses submodel boundary")

        for listing in find(root, ".//a:submodelElementList"):
            children = find(listing, "a:value/*")
            expected = text(listing, "a:typeValueListElement")
            expected_semantic = text(listing, "a:semanticIdListElement/a:keys/a:key/a:value")
            for child in children:
                local = etree.QName(child).localname
                if text(child, "a:idShort"):
                    errors.append("list member has idShort (AASd-120)")
                if expected and local[0].upper() + local[1:] != expected:
                    errors.append("list member type differs from declaration")
                if expected_semantic and semantic(child) != expected_semantic:
                    errors.append("list member semanticId differs (AASd-114)")

        files = find(root, ".//a:file")
        embedded = 0
        external = 0
        provenance = None
        for file in files:
            value = text(file, "a:value")
            if not value:
                continue  # missing source is a coverage issue, not invented bytes
            if value.startswith("/aasx/"):
                embedded += 1
                member = value.lstrip("/")
                if member not in names or not package.read(member):
                    errors.append("missing or empty referenced supplementary file: " + value)
                elif text(file, "a:idShort") == "ConversionProvenance":
                    provenance = json.loads(package.read(member))
            else:
                external += 1
        if provenance is None:
            errors.append("missing conversion provenance record")
        info = package.getinfo("aasx/data.xml")
        supplementary = [n for n in names if n.startswith("aasx/suppl/")]
        counts = {
            "aas": len(shells), "submodels": len(submodels), "entities": len(entities),
            "relationships": len(relationships), "checked_model_references": references,
            "embedded_file_references": embedded, "external_file_references": external,
            "supplementary_files": len(supplementary),
            "step_files": sum(n.endswith(".stp") for n in supplementary),
            "png_files": sum(n.endswith(".png") for n in supplementary),
            "json_files": sum(n.endswith(".json") for n in supplementary),
            "xml_raw_bytes": info.file_size, "xml_zip_bytes": info.compress_size,
            "package_bytes": Path(path).stat().st_size,
        }
    return {
        "package": str(path), "profile": "step2aas-selected-constraints-v1",
        "namespace": ns["a"], "xsd": xsd_result,
        "status": "pass" if not errors else "fail", "errors": errors,
        "counts": counts, "provenance": provenance,
        "limits": "Not complete metamodel/template certification or geometry-fidelity validation.",
    }


def validate_package(path: str | Path, xsd: str | Path | None = None) -> dict:
    """Return a report even when the supplied package cannot be parsed."""
    try:
        return _validate_package(path, xsd)
    except (OSError, zipfile.BadZipFile, KeyError, etree.XMLSyntaxError,
            etree.XMLSchemaParseError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {
            "package": str(path), "profile": "step2aas-selected-constraints-v1",
            "status": "fail", "errors": [f"{type(exc).__name__}: {exc}"],
            "xsd": {"status": "incomplete" if xsd is not None else "not_run"},
            "counts": {}, "provenance": None,
            "limits": "Input parsing failed; subsequent checks were not completed.",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--xsd", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate_package(args.package, args.xsd)
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
