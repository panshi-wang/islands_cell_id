#!/usr/bin/env bash
# Installs dependencies for assign_cell_id.py into a local virtualenv (.venv).
# Usage: ./install.sh
# Supports Debian/Ubuntu (apt) and macOS (Homebrew). On other systems install
# GDAL (libgdal + gdal-config, >= 3.1) yourself first, then run this script.
# Windows: use install.ps1. Any OS: conda env create -f environment.yml
set -euo pipefail

cd "$(dirname "$0")"
VENV_DIR=".venv"

if ! command -v gdal-config >/dev/null 2>&1; then
	if command -v apt-get >/dev/null 2>&1; then
		echo "Installing system GDAL (requires sudo)..."
		sudo apt-get update
		sudo apt-get install -y gdal-bin libgdal-dev python3-venv python3-dev build-essential
	elif command -v brew >/dev/null 2>&1; then
		echo "Installing GDAL with Homebrew..."
		brew install gdal
	else
		echo "gdal-config not found. Install GDAL (>= 3.1) development files (apt/Homebrew)" >&2
		echo "or use conda: conda env create -f environment.yml" >&2
		exit 1
	fi
fi

GDAL_VERSION="$(gdal-config --version)"
echo "System GDAL version: ${GDAL_VERSION}"

python3 -m venv "${VENV_DIR}"
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip setuptools wheel

# Everything except GDAL, which must match the system libgdal version.
grep -v -E '^\s*(#|GDAL==|$)' requirements.txt > "${VENV_DIR}/requirements-nogdal.txt"
python -m pip install -r "${VENV_DIR}/requirements-nogdal.txt"

# GDAL bindings build against libgdal; numpy must already be installed.
python -m pip install --no-build-isolation "GDAL==${GDAL_VERSION}"

python - <<'EOF'
from osgeo import gdal
import geopandas, pyarrow, pandas, numpy
assert gdal.GetDriverByName("COG") is not None, "GDAL COG driver missing (need GDAL >= 3.1)"
print("GDAL", gdal.__version__, "- OK")
EOF

echo "Done. Activate with: source ${VENV_DIR}/bin/activate"
