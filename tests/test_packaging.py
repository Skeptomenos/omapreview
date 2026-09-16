"""Release metadata and deterministic source-asset checks."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "packaging" / "build-source-asset.py"
VERSION = "0.2.0"
SOURCE_URL = (
    f"https://github.com/Skeptomenos/omapreview/releases/download/v{VERSION}/"
    f"omapreview-{VERSION}-src.tar.gz"
)
_HELPER_SPEC = importlib.util.spec_from_file_location("release_asset_helper", HELPER)
assert _HELPER_SPEC and _HELPER_SPEC.loader
_HELPER_MODULE = importlib.util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_HELPER_MODULE)


def _git(repo: Path, *args: str, capture_output: bool = False) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=capture_output,
    )
    return result.stdout.strip() if capture_output else ""


def _init_version() -> str:
    module = ast.parse((ROOT / "src/omepreview/__init__.py").read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    value = ast.literal_eval(node.value)
                    assert isinstance(value, str)
                    return value
    raise AssertionError("__version__ assignment not found")


def _one_match(pattern: str, text: str, label: str) -> str:
    matches = re.findall(pattern, text, flags=re.MULTILINE)
    assert len(matches) == 1, f"expected one {label}, found {len(matches)}"
    return matches[0]


def _sha_from_pkgbuild(text: str) -> str:
    return _one_match(r"^sha256sums=\('([0-9a-f]{64})'\)$", text, "PKGBUILD checksum")


def _sha_from_installer(text: str) -> str:
    return _one_match(r'^TARBALL_SHA256="([0-9a-f]{64})"$', text, "installer checksum")


def _build_asset(repo: Path, output: Path, treeish: str, version: str = VERSION) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "--repo",
            str(repo),
            "--treeish",
            treeish,
            "--version",
            version,
            "--output",
            str(output),
        ],
        check=False,
        text=True,
        capture_output=True,
    )


def test_release_metadata_uses_one_version_source_asset_and_checksum():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    installer = (ROOT / "packaging/install.sh").read_text(encoding="utf-8")
    pkgbuilds = [
        (ROOT / "packaging/PKGBUILD").read_text(encoding="utf-8"),
        (ROOT / "packaging/aur/omapreview/PKGBUILD").read_text(encoding="utf-8"),
    ]

    assert pyproject["project"]["version"] == VERSION
    scripts = pyproject["project"]["scripts"]
    assert scripts["omapreview"] == "omepreview.cli:main"
    assert scripts["omepreview"] == "omepreview.cli:main"
    assert pyproject["project"]["urls"]["Homepage"] == "https://github.com/Skeptomenos/omapreview"
    assert _init_version() == VERSION
    assert _one_match(r'^VERSION="([^"]+)"$', installer, "installer version") == VERSION
    assert all(_one_match(r"^pkgver=(\S+)$", text, "PKGBUILD version") == VERSION for text in pkgbuilds)

    assert f'TARBALL_URL="{SOURCE_URL.replace(VERSION, "${VERSION}")}"' in installer
    for text in pkgbuilds:
        assert "releases/download/v$pkgver/omapreview-$pkgver-src.tar.gz" in text

    hashes = [_sha_from_installer(installer), *(_sha_from_pkgbuild(text) for text in pkgbuilds)]
    assert len(set(hashes)) == 1

    srcinfo = (ROOT / "packaging/aur/omapreview/.SRCINFO").read_text(encoding="utf-8")
    assert f"pkgver = {VERSION}" in srcinfo
    assert f"source = omapreview-{VERSION}-src.tar.gz::{SOURCE_URL}" in srcinfo
    assert f"sha256sums = {hashes[0]}" in srcinfo

    for path in (ROOT / "README.md", ROOT / "index.md", ROOT / "AGENTS.md"):
        text = path.read_text(encoding="utf-8")
        assert f"releases/download/v{VERSION}/install.sh" in text
        assert "releases/download/v0.1.0/install.sh" not in text


def test_source_asset_is_reproducible_and_ignores_packaging_pins(tmp_path):
    repo = tmp_path / "fixture"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.email", "release-fixture@example.invalid")
    _git(repo, "config", "user.name", "release-fixture")
    fixture_files = {
        "pyproject.toml": '[project]\nname = "fixture"\nversion = "0.1.1"\n',
        "src/module.py": "VALUE = 1\n",
        "README.md": "fixture\n",
        "CHANGELOG.md": "fixture changelog\n",
        "LICENSE": "fixture license\n",
        "share/omapreview.desktop": "[Desktop Entry]\n",
        "share/icons/omapreview.png": "synthetic icon\n",
        "skill/SKILL.md": "skill\n",
        "skill/references/cli.md": "cli\n",
        "bin/helper": "#!/bin/sh\n",
        "shell-plugin/widget": "widget\n",
        "docs/reference.md": "docs\n",
        "packaging/PKGBUILD": "sha256sums=('before')\n",
        "tests/test_packaging.py": "repository-only\n",
    }
    for relative, content in fixture_files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "initial")
    first_commit = _git(repo, "rev-parse", "HEAD", capture_output=True)
    first_output = tmp_path / "first.tar.gz"
    repeat_output = tmp_path / "repeat.tar.gz"
    assert _build_asset(repo, first_output, first_commit).returncode == 0
    assert _build_asset(repo, repeat_output, first_commit).returncode == 0
    assert first_output.read_bytes() == repeat_output.read_bytes()
    assert first_output.read_bytes()[4:8] == b"\x00\x00\x00\x00"
    assert hashlib.sha256(first_output.read_bytes()).hexdigest() == hashlib.sha256(
        repeat_output.read_bytes()
    ).hexdigest()

    prefix = f"omapreview-{VERSION}/"
    with tarfile.open(first_output, mode="r:gz") as archive:
        members = archive.getmembers()
        names = {member.name for member in members}
        assert prefix + "CHANGELOG.md" in names
        assert prefix + "skill/SKILL.md" in names
        assert prefix + "share/icons/omapreview.png" in names
        assert not any(name.startswith(prefix + "packaging/") for name in names)
        assert not any(name.startswith(prefix + "tests/") for name in names)
        assert all(member.mtime == _HELPER_MODULE.SOURCE_MTIME for member in members)

    (repo / "packaging/PKGBUILD").write_text("sha256sums=('after-pin')\n", encoding="utf-8")
    _git(repo, "add", "packaging/PKGBUILD")
    _git(repo, "commit", "--quiet", "-m", "pin")
    pin_commit = _git(repo, "rev-parse", "HEAD", capture_output=True)
    pin_output = tmp_path / "pin.tar.gz"
    assert _build_asset(repo, pin_output, pin_commit).returncode == 0
    assert pin_output.read_bytes() == first_output.read_bytes()

    (repo / "src/module.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "add", "src/module.py")
    _git(repo, "commit", "--quiet", "-m", "code")
    code_commit = _git(repo, "rev-parse", "HEAD", capture_output=True)
    code_output = tmp_path / "code.tar.gz"
    assert _build_asset(repo, code_output, code_commit).returncode == 0
    assert code_output.read_bytes() != first_output.read_bytes()


def test_edit_accepts_a_missing_pdf_for_the_launcher():
    from omepreview.cli import build_parser

    args = build_parser().parse_args(["edit"])
    assert args.pdf is None
    args = build_parser().parse_args(["edit", "doc.pdf"])
    assert args.pdf == "doc.pdf"


def test_omapreview_desktop_is_the_launcher_entry():
    text = (ROOT / "share/omapreview.desktop").read_text(encoding="utf-8")
    assert "Name=omapreview\n" in text
    assert "Exec=omapreview edit %f" in text
    assert "TryExec=omapreview" in text
    assert "NoDisplay=true" not in text
    assert "MimeType=application/pdf;" in text


def test_legacy_omepreview_desktop_is_hidden():
    text = (ROOT / "share/omepreview.desktop").read_text(encoding="utf-8")
    assert "NoDisplay=true" in text
    assert "Exec=omapreview edit %f" in text


def test_edit_without_pdf_does_not_open_a_file_dialog():
    import inspect

    from omepreview.cli import cmd_edit

    src = inspect.getsource(cmd_edit)
    assert "pick_pdf_path" not in src
    assert "gui.run" in src


def test_desktop_exec_is_edit_not_a_file_picker():
    text = (ROOT / "share/omapreview.desktop").read_text(encoding="utf-8")
    assert "Exec=omapreview edit %f" in text
    assert "zenity" not in text
    assert "kdialog" not in text
    assert "file-chooser" not in text


def test_installers_expose_cli_aliases_optional_mcp_and_path_guidance():
    for name in ("install.sh", "install-user.sh"):
        script = (ROOT / "packaging" / name).read_text()
        for command in ("omapreview", "omepreview", "omapreview-mcp", "omepreview-mcp"):
            assert command in script
        assert 'export PATH="$HOME/.local/bin:$PATH"' in script
        assert "No shell files were changed" in script
        assert "mcp>=2.2.0" in script
        assert "--system-site-packages" in script
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["scripts"]["omapreview-mcp"] == "omepreview.mcp_entry:main"
    assert project["project"]["dependencies"] == ["pymupdf>=1.24"]


def test_ocr_is_an_independent_optional_extra_and_arch_metadata_matches():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    extras = project["optional-dependencies"]
    assert extras["ocr"] == ["ocrmypdf>=17.11.0,<17.12"]
    assert "ocrmypdf" not in str(project["dependencies"])
    assert "ocrmypdf" not in str(extras["mcp"])
    assert extras["mcp"] == ["mcp>=2.2.0"]
    assert "mcp>=2.2.0" in extras["dev"]

    canonical = (ROOT / "packaging/PKGBUILD").read_text()
    aur = (ROOT / "packaging/aur/omapreview/PKGBUILD").read_text()
    assert canonical == aur
    srcinfo = (ROOT / "packaging/aur/omapreview/.SRCINFO").read_text()
    assert "'python-mcp>=2.2.0:" in canonical
    assert "optdepends = python-mcp>=2.2.0:" in srcinfo
    for package in ("tesseract", "tesseract-data-eng", "ghostscript"):
        assert f"'{package}:" in canonical
        assert f"optdepends = {package}:" in srcinfo
    assert "ocrmypdf" not in canonical
    assert "ocrmypdf" not in srcinfo


_INSTALLER_CORE_PACKAGES = (
    "gtk4", "python", "python-pip", "python-hatchling", "python-gobject",
    "python-cairo", "python-pymupdf",
)


def _run_release_installer(tmp_path, options=(), *, installed=None, sudo_exit=0, ocr_available=True):
    """Run the installer against a synthetic asset and inert external commands."""
    import io
    import json
    import os

    tmp_path.mkdir(parents=True, exist_ok=True)
    script = (ROOT / "packaging/install.sh").read_text()
    version = _one_match(r'^VERSION="([^"]+)"$', script, "installer version")
    asset = tmp_path / "source.tar.gz"
    with tarfile.open(asset, "w:gz") as archive:
        for name, body in {
            "pyproject.toml": '[project]\nname = "fixture"\n',
            "share/omapreview.desktop": "[Desktop Entry]\nExec=omapreview edit %f\nTryExec=omapreview\n",
        }.items():
            payload = body.encode()
            member = tarfile.TarInfo(f"omapreview-{version}/{name}")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))

    # Keep HOME unchanged. Redirect only the install tree, use the fixture's
    # real checksum, and simulate an ordinary user for root-run CI as well.
    local = tmp_path / "user-local"
    script = script.replace("${HOME}/.local", str(local))
    script = script.replace("[[ ${EUID} -eq 0 ]]", "[[ 1000 -eq 0 ]]")
    script = re.sub(r'^TARBALL_SHA256="[0-9a-f]{64}"$',
                    f'TARBALL_SHA256="{hashlib.sha256(asset.read_bytes()).hexdigest()}"',
                    script, flags=re.MULTILINE)
    installer = tmp_path / "install.sh"
    installer.write_text(script)
    commands = tmp_path / "commands.jsonl"
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    runner = stubs / "runner"
    runner.write_text("#!" + sys.executable + "\n" + r'''
import json, os, shutil, sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
assert os.environ['HOME'] == os.environ['INSTALLER_TEST_ORIGINAL_HOME']
with Path(os.environ['INSTALLER_TEST_COMMANDS']).open('a') as log:
    log.write(json.dumps({'command': name, 'args': args}) + '\n')
if name == 'pacman':
    assert args == ['-Qq'], args
    print(os.environ['INSTALLER_TEST_PACKAGES'])
elif name == 'sudo':
    assert args[:4] == ['pacman', '-S', '--needed', '--noconfirm'], args
    sys.exit(int(os.environ['INSTALLER_TEST_SUDO_EXIT']))
elif name == 'curl':
    shutil.copyfile(os.environ['INSTALLER_TEST_ASSET'], args[args.index('-o') + 1])
elif name in ('python3', 'python'):
    if args[:2] == ['-m', 'venv']:
        target = Path(args[-1]) / 'bin'
        target.mkdir(parents=True, exist_ok=True)
        for command in ('python', 'pip', 'omapreview', 'omepreview', 'omapreview-mcp', 'omepreview-mcp'):
            link = target / command
            if not link.exists():
                link.symlink_to(os.environ['INSTALLER_TEST_RUNNER'])
    else:
        assert args == ['-m', 'pip', 'install', '--upgrade', 'pip'], args
elif name == 'pip':
    assert args[0] == 'install', args
elif name == 'omapreview':
    if args == ['--version']:
        print('omapreview fixture')
    else:
        assert args == ['ocr-status'], args
        available = os.environ['INSTALLER_TEST_OCR_AVAILABLE'] == '1'
        print(json.dumps({'available': available, 'languages': ['eng'] if available else [],
                          'code': None if available else 'dependency_missing'}))
else:
    assert name in ('gtk-update-icon-cache', 'update-desktop-database', 'omarchy-refresh-applications'), name
''')
    runner.chmod(0o755)
    for name in ("pacman", "sudo", "curl", "python3", "python", "gtk-update-icon-cache",
                 "update-desktop-database", "omarchy-refresh-applications"):
        (stubs / name).symlink_to(runner)
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    env = {
        **os.environ,
        "PATH": str(stubs) + os.pathsep + os.environ.get("PATH", os.defpath),
        "TMPDIR": str(temporary),
        "INSTALLER_TEST_ORIGINAL_HOME": os.environ["HOME"],
        "INSTALLER_TEST_COMMANDS": str(commands),
        "INSTALLER_TEST_PACKAGES": "\n".join(_INSTALLER_CORE_PACKAGES if installed is None else installed),
        "INSTALLER_TEST_SUDO_EXIT": str(sudo_exit),
        "INSTALLER_TEST_ASSET": str(asset),
        "INSTALLER_TEST_RUNNER": str(runner),
        "INSTALLER_TEST_OCR_AVAILABLE": "1" if ocr_available else "0",
    }
    result = subprocess.run(["bash", str(installer), *options], env=env, text=True,
                            capture_output=True, check=False, timeout=20)
    calls = [json.loads(line) for line in commands.read_text().splitlines()]
    return result, calls, local


def test_release_installer_selects_independent_extras_without_sudo(tmp_path):
    for index, (options, suffix) in enumerate((
        ((), ""), (("--with-mcp",), "[mcp]"), (("--with-ocr",), "[ocr]"),
        (("--with-ocr", "--with-mcp"), "[ocr,mcp]"),
    )):
        result, calls, local = _run_release_installer(tmp_path / str(index), options)
        assert result.returncode == 0, result.stderr
        assert calls[0] == {"command": "pacman", "args": ["-Qq"]}
        assert not any(call["command"] == "sudo" for call in calls)
        assert not any(call["command"] == "omarchy-refresh-applications" for call in calls)
        assert [call["args"] for call in calls if call["command"] == "pip"] == [
            ["install", str(local / "share/omapreview/src") + suffix]
        ]
        status_calls = [call for call in calls if call["args"] == ["ocr-status"]]
        assert len(status_calls) == int("--with-ocr" in options)
        desktop = (local / "share/applications/omapreview.desktop").read_text()
        assert f"Exec={local}/share/omapreview/venv/bin/omapreview edit %f" in desktop
        assert (local / "bin/omapreview").is_symlink()
        assert "v0.1.1 has no OCR action" not in result.stdout + result.stderr


def test_release_installer_installs_only_missing_core_packages(tmp_path):
    missing = {"python-cairo", "python-pymupdf"}
    installed = [package for package in _INSTALLER_CORE_PACKAGES if package not in missing]
    # Similar names must not count as the exact installed package.
    installed.append("python-cairo-extra")
    result, calls, _local = _run_release_installer(tmp_path, ("--with-ocr",), installed=installed)
    assert result.returncode == 0, result.stderr
    assert [call["args"] for call in calls if call["command"] == "sudo"] == [
        ["pacman", "-S", "--needed", "--noconfirm", "python-cairo", "python-pymupdf"]
    ]


def test_release_installer_stops_if_missing_core_install_fails(tmp_path):
    installed = [package for package in _INSTALLER_CORE_PACKAGES if package != "gtk4"]
    result, calls, local = _run_release_installer(tmp_path, installed=installed, sudo_exit=1)
    assert result.returncode == 1
    assert [call["command"] for call in calls] == ["pacman", "sudo"]
    assert not local.exists()


def test_release_installer_reports_missing_ocr_dependencies_without_installing_them(tmp_path):
    result, calls, _local = _run_release_installer(tmp_path, ("--with-ocr", "--with-mcp"),
                                                 ocr_available=False)
    assert result.returncode == 0, result.stderr
    assert '"available": false' in result.stdout and '"languages": []' in result.stdout
    assert "tesseract, tesseract-data-eng and ghostscript yourself" in result.stdout
    assert not any(call["command"] == "sudo" for call in calls)
