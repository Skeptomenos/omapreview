"""Private exec shim: set inherited limits without preexec_fn in threaded clients.

The parent owns cancellation, process-group cleanup and the private directory.
No optional package is imported in the application interpreter.
"""
from __future__ import annotations

import os
import resource
import sys


def main():
    executable, seconds, *arguments = sys.argv[1:]
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
    os.execv(executable, [executable, *arguments])


if __name__ == "__main__":
    main()
