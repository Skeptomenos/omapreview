"""Reproducible bounded O1 resource/preservation measurement (synthetic only).

python tests/ocr_engine_probe.py NEW_DIRECTORY
Select the backend with OMAPREVIEW_OCRMYPDF. No installation or GUI access.
"""
from __future__ import annotations
import json
import resource
import sys
import time
from pathlib import Path
import pymupdf as fitz
from omepreview.engine import apply
from ocr_fixtures import add_scan, fingerprint, assert_ocr_report


def main(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    source=root/'larger-source.pdf';target=root/'larger-output.pdf'
    expected=[]
    with fitz.open() as doc:
        for index in range(12):
            label=f'SYNTHETIC BATCH PAGE {index+1:02d} TOTAL 12345'
            add_scan(doc,label)
            expected.append({'status':'processed','contains':label})
        doc.save(source,deflate=True)
    case={'sha256':fingerprint(source),'pages':expected}
    started=time.monotonic();events=[]
    proposal=apply(source,[{'op':'ocr'}],output=target,dry_run=True)
    result=apply(source,[{'op':'ocr','expected_source_sha256':proposal['applied'][0]['ocr']['source_sha256']}],output=target,dry_run=False,progress=events.append)
    observations=assert_ocr_report(source,target,case,result)
    summary={'pages':12,'input_bytes':source.stat().st_size,'output_bytes':target.stat().st_size,'elapsed_including_preflight_and_oracle':round(time.monotonic()-started,3),'parent_peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'result':result,'observations':observations,'phases':events,'limits':'100 pages; 500 MiB input; 100 MP source/verification rasters; worker per-process 4 GiB address space, 2 GiB file size, timeout+2 CPU seconds, 256 FDs; total temporary storage checked at 50 ms intervals, not a hard quota. Parent PyMuPDF calls are bounded by input/raster/page limits but are not interruptible inside a native call. RSS samples omit processes that start and exit between samples.'}
    (root/'results.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps({k:v for k,v in summary.items() if k not in ('result','observations')},indent=2))
    print(json.dumps(result['applied'][0]['ocr']['resources']))


if __name__=='__main__':main(sys.argv[1])
