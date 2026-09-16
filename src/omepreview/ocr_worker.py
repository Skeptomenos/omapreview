"""Private backend worker with inherited limits and a second plugin gate.

Runs in the selected backend interpreter. The application imports no optional
package. The parent owns the process group, deadline and private directory.
"""
from __future__ import annotations

import errno
from importlib import metadata
import os
from pathlib import Path
import resource
import runpy
import sys


def main():
    executable, seconds, expected_version, status_file, *arguments = sys.argv[1:]
    limits = {
        resource.RLIMIT_CORE: 0,
        resource.RLIMIT_FSIZE: 2 * 1024**3,
        resource.RLIMIT_AS: 4 * 1024**3,
        resource.RLIMIT_CPU: int(seconds) + 2,
        resource.RLIMIT_NOFILE: 256,
    }
    for name, bound in limits.items():
        _soft, hard = resource.getrlimit(name)
        effective = min(bound, hard) if hard != resource.RLIM_INFINITY else bound
        resource.setrlimit(name, (effective, effective))
        if resource.getrlimit(name) != (effective, effective):
            raise RuntimeError("Could not enforce OCR worker resource limits")
    os.umask(0o077)
    # Match the selected console launcher's import environment without removing
    # PYTHONPATH or importing entry-point modules to inspect them.
    sys.path[0] = str(Path(executable).parent)
    try:
        # Empty expected_version is used only by isolated process-limit probes.
        # Production capability reports always supply the accepted version.
        if expected_version:
            try:
                accepted = (metadata.version('ocrmypdf') == expected_version
                            and not metadata.entry_points(group='ocrmypdf'))
            except metadata.PackageNotFoundError:
                accepted = False
            if not accepted:
                Path(status_file).write_text('{"code":"dependency_unsupported"}')
                raise SystemExit(91)
        sys.argv = [executable, *arguments]
        # Keep the shim around to identify resource exceptions that reach the
        # launcher boundary. Generic backend exit codes remain worker_failed.
        runpy.run_path(executable, run_name='__main__')
    except MemoryError:
        Path(status_file).write_text('{"code":"resource_limit"}')
        raise SystemExit(90)
    except OSError as exc:
        if exc.errno not in (errno.EFBIG, errno.ENOMEM):
            raise
        Path(status_file).write_text('{"code":"resource_limit"}')
        raise SystemExit(90)


if __name__ == "__main__":
    main()
