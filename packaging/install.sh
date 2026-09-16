#!/usr/bin/env bash
# Visitor install for omapreview 0.2.0 (no git clone of the app repo).
# Arch / Omarchy: pacman deps, GitHub release asset, ~/.local command + desktop.
#
#   curl -fsSL https://github.com/Skeptomenos/omapreview/releases/download/v0.2.0/install.sh | bash
set -euo pipefail

extras=()
with_ocr=false
for option in "$@"; do
  case "${option}" in
    --with-ocr) extras+=(ocr); with_ocr=true ;;
    --with-mcp) extras+=(mcp) ;;
    *) echo "Usage: bash install.sh [--with-ocr] [--with-mcp]" >&2; exit 2 ;;
  esac
done

VERSION="0.2.0"
# This release asset is built from the verified application tree so
# Super+Space opens an empty editor without requiring a git clone.
TARBALL_URL="https://github.com/Skeptomenos/omapreview/releases/download/v${VERSION}/omapreview-${VERSION}-src.tar.gz"
TARBALL_SHA256="9c5dcfd5e724c7063fd401b576c599feed0f500426a55e24d45d1f173e994d91"
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

core_packages=(gtk4 python python-pip python-hatchling python-gobject python-cairo python-pymupdf)
installed_packages="$(pacman -Qq)"
missing_packages=()
for package in "${core_packages[@]}"; do
  case $'\n'"${installed_packages}"$'\n' in
    *$'\n'"${package}"$'\n'*) ;;
    *) missing_packages+=("${package}") ;;
  esac
done
if (( ${#missing_packages[@]} )); then
  sudo pacman -S --needed --noconfirm "${missing_packages[@]}"
fi

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
"${VENV}/bin/python" -m venv --system-site-packages "${VENV}"
"${VENV}/bin/python" -m pip install --upgrade pip
install_target="${SRC}"
if (( ${#extras[@]} )); then
  IFS=,
  install_target="${SRC}[${extras[*]}]"
  unset IFS
fi
"${VENV}/bin/pip" install "${install_target}"

ln -sfn "${VENV}/bin/omapreview" "${BIN_DIR}/omapreview"
ln -sfn "${VENV}/bin/omepreview" "${BIN_DIR}/omepreview"
ln -sfn "${VENV}/bin/omapreview-mcp" "${BIN_DIR}/omapreview-mcp"
ln -sfn "${VENV}/bin/omepreview-mcp" "${BIN_DIR}/omepreview-mcp"

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
case ":${PATH}:" in
  *":${BIN_DIR}:"*) ;;
  *) echo 'Terminal setup: export PATH="$HOME/.local/bin:$PATH" (current shell only).'
     echo "Or use ${BIN_DIR}/omapreview directly. No shell files were changed." ;;
esac

echo "installed: ${BIN_DIR}/omapreview -> ${exec_path}"
echo "desktop:   ${DESKTOP_DST}"
"${exec_path}" --version
echo "Open from the Omarchy menu: Super+Space, type omapreview, Enter."
echo "The editor starts empty — Open (Ctrl+O) picks a PDF. No file dialog in Exec."
echo "If it is missing, run: omarchy restart shell"

echo "Optional MCP: ${VENV}/bin/python -m pip install 'mcp>=2.2.0'"
echo "Then launch: ${BIN_DIR}/omapreview-mcp (stdio; no checkout required)."
echo "Optional OCR: rerun this installer with --with-ocr."
echo "OCR dependency/language status: ${BIN_DIR}/omapreview ocr-status"
if [[ "${with_ocr}" == true ]]; then
  "${exec_path}" ocr-status
  echo "If OCR system tools or English data are missing, install tesseract, tesseract-data-eng and ghostscript yourself."
  echo "Install other Tesseract language packs only when you need them, then recheck ocr-status."
fi
