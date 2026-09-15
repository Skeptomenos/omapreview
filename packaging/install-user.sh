#!/usr/bin/env bash
# Install omapreview for this user: venv + ~/.local/bin + desktop entry.
# Run from a clone (on omarchy-air: ~/omepreview). Does not need root.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN_DIR="${HOME}/.local/bin"
APP_DIR="${HOME}/.local/share/applications"
VENV="${ROOT}/.venv"
DESKTOP_SRC="${ROOT}/share/omapreview.desktop"
DESKTOP_DST="${APP_DIR}/omapreview.desktop"

if [[ ! -f "${ROOT}/pyproject.toml" ]]; then
  echo "install-user.sh: expected a checkout at ${ROOT}" >&2
  exit 1
fi

if [[ ! -x "${VENV}/bin/python" ]]; then
  PY="$(command -v python3 || command -v python)"
  "${PY}" -m venv --system-site-packages "${VENV}"
fi

"${VENV}/bin/pip" install -e "${ROOT}"

mkdir -p "${BIN_DIR}" "${APP_DIR}"
install -Dm644 "${ROOT}/share/icons/omapreview.png" \
  "${HOME}/.local/share/icons/hicolor/512x512/apps/omapreview.png"
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "${HOME}/.local/share/icons/hicolor"
fi
ln -sfn "${VENV}/bin/omapreview" "${BIN_DIR}/omapreview"
# Keep the Python package command working in a terminal too.
ln -sfn "${VENV}/bin/omepreview" "${BIN_DIR}/omepreview"

# gtk-launch / uwsm-app do not always see ~/.local/bin. Pin Exec to the venv.
exec_path="${VENV}/bin/omapreview"
sed \
  -e "s|^Exec=omapreview |Exec=${exec_path} |" \
  -e "s|^TryExec=omapreview$|TryExec=${exec_path}|" \
  "${DESKTOP_SRC}" > "${DESKTOP_DST}"
# Hide a leftover launcher name from before the omapreview rename.
rm -f "${APP_DIR}/omepreview.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${APP_DIR}"
fi

# Omarchy 4 copies its own .desktop files then runs update-desktop-database.
# It does not delete ours. Walker is gone; the menu watches DesktopEntries.
if command -v omarchy-refresh-applications >/dev/null 2>&1; then
  omarchy-refresh-applications
fi

echo "installed: ${BIN_DIR}/omapreview -> ${exec_path}"
echo "desktop:   ${DESKTOP_DST}"
"${exec_path}" --version
echo "Open from the Omarchy menu: Super+Space, type omapreview, Enter."
echo "The editor starts empty — Open (Ctrl+O) picks a PDF."
echo "If it is missing, run: omarchy restart shell"
