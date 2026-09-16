"""O0 real-backend experiment, not the production OCR implementation.

python tests/ocr_probe.py NEW_DIRECTORY /path/to/ocrmypdf
Keeps generated inputs/outputs and structured observations for independent review.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pymupdf as fitz
from ocr_fixtures import assert_saved_copy, build, fingerprint, inspect_document, inspect_pair


def main(root, executable):
    root = Path(root)
    cases = build(root / "fixtures")
    results = {"cases": {}, "normalization": {}, "commands": []}

    def run(source, name, selected=None):
        target = root / f"{name}.pdf"
        cmd = [executable, "--mode", "skip", "--output-type", "pdf", "--optimize", "0", "--jobs", "2", "--language", "eng", "--tesseract-timeout", "30"]
        if selected:
            cmd += ["--pages", ",".join(map(str, selected))]
        cmd += [str(source), str(target)]
        started = time.monotonic()
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        (root / f"{name}.log").write_text(p.stdout + p.stderr)
        results["commands"].append(cmd)
        return target, {"exit_code": p.returncode, "seconds": round(time.monotonic()-started, 3), "output_sha256": fingerprint(target) if target.exists() else None}

    for name, case in cases.items():
        source = root / "fixtures" / case["file"]
        # Backend-only preservation probes intentionally bypass proposed v1 refusal.
        if name in ("encrypted", "owner_encrypted", "signature_structure", "unsigned_signature", "redaction"):
            continue
        target, record = run(source, name + "-raw", case.get("selected"))
        if record["exit_code"] == 0:
            expected = case["pages"] or [{}]
            record["pages"] = inspect_pair(source, target, expected)
            record["document"] = inspect_document(source, target)
            with fitz.open(source) as a, fitz.open(target) as b:
                def structure(d):
                    return {"widgets": [[(w.field_name, w.field_value, list(w.rect), w.field_type) for w in p.widgets()] for p in d], "annotations": [[(a.type, a.info, list(a.rect)) for a in p.annots()] for p in d], "links": [[{k:v for k,v in l.items() if k not in ('xref', 'id')} for l in p.get_links()] for p in d], "embedded": {n: fingerprint_bytes(d.embfile_get(n)) for n in d.embfile_names()}}
                record["structure_equal"] = structure(a) == structure(b)
        results["cases"][name] = record
    source = root / "fixtures" / "geometry.pdf"
    normalized = root / "normalized.pdf"
    with fitz.open(source) as d:
        rotations = [p.rotation for p in d]
        xml_metadata = d.get_xml_metadata()
        metadata = {k: v for k, v in d.metadata.items() if k not in ("format", "encryption")}
        for p in d:
            p.set_rotation(0)
        d.save(normalized)
    target, record = run(normalized, "normalized-ocr")
    restored = root / "restored.pdf"
    if record["exit_code"] == 0:
        with fitz.open(target) as d:
            for p, rotation in zip(d, rotations):
                p.set_rotation(rotation)
            d.set_metadata(metadata)
            if xml_metadata:
                d.set_xml_metadata(xml_metadata)
            else:
                d.del_xml_metadata()
            d.save(restored)
        record["document"] = inspect_document(source, restored)
        record["pages"] = assert_saved_copy(source, restored, cases["geometry"]["pages"])
        record["output_sha256"] = fingerprint(restored)
    results["normalization"] = record
    results["source_preserved"] = all(fingerprint(root / "fixtures" / c["file"]) == c["sha256"] for c in cases.values())
    (root / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    assert results["source_preserved"]
    assert record["exit_code"] == 0
    assert all(all(p[k] for k in ("geometry_equal", "pixels_equal", "word_bounds_valid", "expected_text")) for p in record["pages"])
    print(json.dumps(results, indent=2))


def fingerprint_bytes(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


if __name__ == "__main__":
    main(*sys.argv[1:])
