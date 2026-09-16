"""Synthetic O0 corpus and independent saved-PDF oracle. No application writer.

Run: python tests/ocr_fixtures.py NEW_DIRECTORY
All files are generated; no signatures or external images are read.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf as fitz

SCAN = "SCANNED INVOICE TOTAL 123.45"
CONTROL = "DIGITAL CONTROL TEXT"
ROTATED = "ROTATED SCAN REFERENCE 67890"


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def raster(label=SCAN, *, sideways=False, poor=False, photo=False):
    with fitz.open() as doc:
        page = doc.new_page(width=595, height=842)
        if photo:
            # Synthetic non-text picture, not a claim about all photographs.
            page.draw_circle((280, 280), 110, color=(.2, .5, .8), fill=(.2, .5, .8))
            page.draw_rect((60, 500, 500, 700), color=(.3, .7, .2), fill=(.3, .7, .2))
        elif label:
            page.insert_text((72, 120), label, fontsize=22, color=(.995,)*3 if poor else (0,)*3)
        if sideways:
            page.set_rotation(90)
        return page.get_pixmap(dpi=35 if poor else 200)


def add_scan(doc, label=SCAN, **kwargs):
    pix = raster(label, **kwargs)
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, pixmap=pix)
    return page


def build(directory):
    """Refuse an existing corpus directory; return JSON-friendly expectations."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    cases = {}

    def save(name, doc, outcomes, **extra):
        path = directory / f"{name}.pdf"
        doc.save(path, deflate=True)
        cases[name] = {"file": path.name, "pages": outcomes, **extra}

    with fitz.open() as d:
        add_scan(d)
        save("clean", d, [{"status": "processed", "contains": SCAN}])
    with fitz.open() as d:
        d.new_page().insert_text((72, 120), CONTROL, fontsize=22)
        save("native", d, [{"status": "skipped", "reason": "existing_text", "contains": CONTROL}])
    with fitz.open() as d:
        p = add_scan(d)
        p.insert_text((72, 300), CONTROL, fontsize=22)
        save("mixed_page", d, [{"status": "skipped", "reason": "existing_text", "contains": CONTROL, "absent": SCAN}])
    with fitz.open() as d:
        for rotation in (0, 90, 180, 270):
            p = add_scan(d, ROTATED)
            p.set_cropbox(fitz.Rect(20, 30, 575, 812))
            p.set_rotation(rotation)
        d[0].set_trimbox(fitz.Rect(40, 50, 550, 790))
        d[0].set_bleedbox(fitz.Rect(30, 40, 560, 800))
        d[0].set_artbox(fitz.Rect(60, 70, 530, 770))
        d.set_metadata({"title": "Synthetic geometry", "author": "O0 fixture", "subject": "Preservation gate", "keywords": "synthetic,ocr", "creator": "omapreview O0", "producer": "O0 fixture builder", "creationDate": "D:20260102030405Z", "modDate": "D:20260102030405Z"})
        d.set_xml_metadata('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" dc:description="SYNTHETIC XMP"/></rdf:RDF></x:xmpmeta>')
        d.set_toc([[1, "Synthetic start", 1], [1, "Rotated control", 3]])
        d.set_page_labels([{"startpage": 0, "prefix": "S-", "style": "D", "firstpagenum": 7}])
        save("geometry", d, [{"status": "processed", "contains": ROTATED}] * 4)
    with fitz.open() as d:
        add_scan(d, sideways=True)
        save("sideways_pixels", d, [{"status": "needs_review", "reason": "no_text"}], accuracy_assertion=False)
    for name, kwargs, outcome in (
        ("blank", {"label": ""}, {"status": "blank"}),
        ("photo", {"photo": True}, {"status": "needs_review", "reason": "no_text"}),
        ("poor", {"poor": True}, {"status": "processed", "review_required": True, "known_transcription_error": True}),
    ):
        with fitz.open() as d:
            add_scan(d, **kwargs)
            save(name, d, [outcome])
    with fitz.open() as d:
        add_scan(d)
        d.new_page().insert_text((72, 120), CONTROL, fontsize=22)
        add_scan(d, ROTATED)
        save("selected", d, [{"status": "processed", "contains": SCAN}, {"status": "skipped", "reason": "not_selected", "contains": CONTROL}, {"status": "skipped", "reason": "not_selected", "absent": ROTATED}], selected=[1])
    with fitz.open() as d:
        p = add_scan(d)
        p.insert_text((72, 120), SCAN, fontsize=22, render_mode=3)
        save("existing_ocr", d, [{"status": "skipped", "reason": "existing_text", "contains": SCAN}])
    for name in ("comment", "widget", "link", "embedded", "redaction", "signature_structure", "unsigned_signature"):
        with fitz.open() as d:
            p = add_scan(d)
            if name == "comment":
                p.add_text_annot((330, 180), "SYNTHETIC NOTE")
            elif name in ("widget", "unsigned_signature", "signature_structure"):
                w = fitz.Widget()
                w.field_name = "synthetic"
                w.field_type = fitz.PDF_WIDGET_TYPE_TEXT if name == "widget" else fitz.PDF_WIDGET_TYPE_SIGNATURE
                w.rect = fitz.Rect(72, 200, 250, 230)
                if name == "widget":
                    w.field_value = "SYNTHETIC VALUE"
                widget = p.add_widget(w)
                if name == "signature_structure":
                    xref = d.get_new_xref()
                    d.update_object(xref, "<< /Type /Sig /Filter /Adobe.PPKLite /SubFilter /adbe.pkcs7.detached /ByteRange [0 1 2 3] /Contents <0000> >>")
                    d.xref_set_key(widget.xref, "V", f"{xref} 0 R")
            elif name == "link":
                p.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(72, 220, 260, 245), "uri": "https://example.invalid/synthetic"})
            elif name == "embedded":
                d.embfile_add("synthetic.txt", b"SYNTHETIC ATTACHMENT\n")
            elif name == "redaction":
                p.add_redact_annot(fitz.Rect(72, 90, 360, 130))
            # Narrow v1: document-level refusal even on unselected pages.
            code = {"redaction": "pending_redactions", "signature_structure": "signature_structure", "unsigned_signature": "signature_structure"}.get(name, "unsupported_content")
            save(name, d, [], refusal=code, structure=name)
    for owner_only in (False, True):
        with fitz.open() as d:
            add_scan(d)
            name = "owner_encrypted" if owner_only else "encrypted"
            path = directory / f"{name}.pdf"
            d.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="synthetic-owner", user_pw="" if owner_only else "synthetic-user")
            cases[name] = {"file": path.name, "pages": [], "refusal": "encrypted"}
    for case in cases.values():
        case["sha256"] = fingerprint(directory / case["file"])
    (directory / "manifest.json").write_text(json.dumps(cases, indent=2) + "\n")
    return cases


def inspect_pair(source, output, expected):
    """Strict fixture oracle: geometry, text and RGB renders are separate gates.

    Never use fixture needles or inferred fixture outcomes as production confidence.
    """
    observations = []
    with fitz.open(source) as before, fitz.open(output) as after:
        assert len(before) == len(after) == len(expected), "page_count"
        for a, b, spec in zip(before, after, expected):
            geometry = all(getattr(a, key) == getattr(b, key) for key in ("mediabox", "cropbox", "trimbox", "bleedbox", "artbox", "rotation"))
            x, y = a.get_pixmap(dpi=100), b.get_pixmap(dpi=100)
            pixels = (x.width, x.height, x.samples) == (y.width, y.height, y.samples)
            text = b.get_text().strip()
            words = b.get_text("words")
            bounds = fitz.Rect(0, 0, b.cropbox.width, b.cropbox.height)
            word_bounds = all(bounds.contains(fitz.Rect(w[:4])) for w in words)
            observation = {"page": a.number + 1, "geometry_equal": geometry, "pixels_equal": pixels, "word_bounds_valid": word_bounds, "text": text, "word_count": len(words)}
            if "contains" in spec:
                observation["expected_text"] = spec["contains"] in text
                if spec["contains"] == ROTATED:
                    # Ground-truth scan text is at MediaBox x=72, baseline=120.
                    region = fitz.Rect(50, 65, 450, 105)
                    observation["text_placement"] = bool(words) and all(region.contains(fitz.Rect(w[:4])) for w in words)
            if "absent" in spec:
                observation["absent_text"] = spec["absent"] not in text
            observations.append(observation)
    return observations


def _outline(doc):
    # Object numbers are storage identities; destinations and styling are content.
    return [[*entry[:3], {k: v for k, v in entry[3].items() if k != "xref"}]
            for entry in doc.get_toc(simple=False)]


def inspect_document(source, output):
    """Metadata, simple internal outlines and page labels have independent gates."""
    with fitz.open(source) as a, fitz.open(output) as b:
        keys = ("title", "author", "subject", "keywords", "creator", "producer", "creationDate", "modDate")
        return {
            "metadata_equal": all(a.metadata[k] == b.metadata[k] for k in keys),
            "xml_metadata_equal": a.get_xml_metadata() == b.get_xml_metadata(),
            "outlines_equal": _outline(a) == _outline(b),
            "page_labels_equal": a.get_page_labels() == b.get_page_labels(),
        }


def assert_saved_copy(source, output, expected):
    """Reusable O1/O3/O5 gate; returns inspectable evidence on success."""
    document = inspect_document(source, output)
    assert all(document.values()), f"document preservation failed: {document}"
    observations = inspect_pair(source, output, expected)
    for page in observations:
        for key in ("geometry_equal", "pixels_equal", "word_bounds_valid", "expected_text", "absent_text", "text_placement"):
            if key in page:
                assert page[key], f"page {page['page']}: {key} failed"
    return observations


def assert_ocr_report(source, output, case, result):
    """Future implementation acceptance: disk evidence and report must agree."""
    assert "refusal" not in case, "Run refusal cases through preflight, not this oracle"
    assert fingerprint(source) == case["sha256"], "source changed"
    assert Path(result["output"]).resolve() == Path(output).resolve()
    assert len(result["applied"]) == 1
    op = result["applied"][0]
    assert op["op"] == "ocr" and op["applied"] is True
    report = op["ocr"]
    assert report["source_sha256"] == case["sha256"]
    assert report["output_sha256"] == fingerprint(output)
    observations = assert_saved_copy(source, output, case["pages"])
    assert len(report["pages"]) == len(observations)
    for expected, observed, page in zip(case["pages"], observations, report["pages"]):
        assert page["page"] == observed["page"]
        assert page["status"] == expected["status"]
        if "reason" in expected:
            assert page["reason"] == expected["reason"]
        assert page["text_after_chars"] == len(observed["text"])
        assert page["word_count"] == observed["word_count"]
        if page["status"] == "processed":
            assert observed["word_count"] > 0
            assert page["review_required"] is True
    assert report["recognized_pages"] == sum(p["status"] == "processed" for p in report["pages"])
    assert report["status"] == ("needs_review" if any(p["status"] == "needs_review" for p in report["pages"]) else "complete")
    return observations


if __name__ == "__main__":
    import sys
    print(json.dumps(build(sys.argv[1]), indent=2))
