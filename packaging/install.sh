#!/usr/bin/env bash
# Visitor install for omapreview 0.1.1 (no git clone of the app repo).
# Arch / Omarchy: pacman deps, GitHub release asset, ~/.local command + desktop.
#
#   curl -fsSL https://github.com/Skeptomenos/omapreview/releases/download/v0.1.1/install.sh | bash
set -euo pipefail

VERSION="0.1.1"
# This release asset is built from the verified application tree so
# Super+Space opens an empty editor without requiring a git clone.
TARBALL_URL="https://github.com/Skeptomenos/omapreview/releases/download/v${VERSION}/omapreview-${VERSION}-src.tar.gz"
TARBALL_SHA256="f75aa55b4edd85ecde36401c223d54b7e421af9905ea125da36b483d1f488c12"
PREFIX="${HOME}/.local/share/omapreview"
BIN_DIR="${HOME}/.local/bin"
APP_DIR="${HOME}/.local/share/applications"
SRC="${PREFIX}/src"
VENV="${PREFIX}/venv"
DESKTOP_DST="${APP_DIR}/omapreview.desktop"

if [[ ${EUID} -eq 0 ]]; then
  echo "omapreview install: run as your user (the script sudo's pacman). Do not pipe as root." >&2
  exit 1
fi

if ! command -v pacman >/dev/null 2>&1; then
  echo "omapreview install: pacman not found — this path is Arch/Omarchy only." >&2
  exit 1
fi

sudo pacman -S --needed --noconfirm \
  gtk4 python python-pip python-hatchling python-gobject python-cairo python-pymupdf

workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT
tarball="${workdir}/omapreview-${VERSION}.tar.gz"
curl -fsSL -o "${tarball}" "${TARBALL_URL}"
echo "${TARBALL_SHA256}  ${tarball}" | sha256sum -c -

mkdir -p "${PREFIX}" "${BIN_DIR}" "${APP_DIR}"
rm -rf "${SRC}"
tar -xzf "${tarball}" -C "${workdir}"
mv "${workdir}/omapreview-${VERSION}" "${SRC}"

if [[ ! -f "${SRC}/pyproject.toml" || ! -f "${SRC}/share/omapreview.desktop" ]]; then
  echo "omapreview install: tarball missing pyproject.toml or desktop file" >&2
  exit 1
fi

PY="$(command -v python3 || command -v python)"
if [[ ! -x "${VENV}/bin/python" ]]; then
  "${PY}" -m venv --system-site-packages "${VENV}"
fi
"${VENV}/bin/python" -m pip install --upgrade pip
"${VENV}/bin/pip" install "${SRC}"

ln -sfn "${VENV}/bin/omapreview" "${BIN_DIR}/omapreview"
ln -sfn "${VENV}/bin/omepreview" "${BIN_DIR}/omepreview"

exec_path="${VENV}/bin/omapreview"
sed \
  -e "s|^Exec=omapreview |Exec=${exec_path} |" \
  -e "s|^TryExec=omapreview$|TryExec=${exec_path}|" \
  "${SRC}/share/omapreview.desktop" > "${DESKTOP_DST}"
rm -f "${APP_DIR}/omepreview.desktop"

if [[ -f "${SRC}/share/icons/omapreview.png" ]]; then
  install -Dm644 "${SRC}/share/icons/omapreview.png" \
    "${HOME}/.local/share/icons/hicolor/512x512/apps/omapreview.png"
  if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "${HOME}/.local/share/icons/hicolor"
  fi
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${APP_DIR}"
fi
if command -v omarchy-refresh-applications >/dev/null 2>&1; then
  omarchy-refresh-applications
fi

case ":${PATH}:" in
  *":${BIN_DIR}:"*) ;;
  *) echo "note: add ${BIN_DIR} to PATH for the terminal command (desktop Exec is absolute)." ;;
esac

echo "installed: ${BIN_DIR}/omapreview -> ${exec_path}"
echo "desktop:   ${DESKTOP_DST}"
"${exec_path}" --version
echo "Open from the Omarchy menu: Super+Space, type omapreview, Enter."
echo "The editor starts empty — Open (Ctrl+O) picks a PDF. No file dialog in Exec."
echo "If it is missing, run: omarchy restart shell"
