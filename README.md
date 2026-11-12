# islands_cell_id

`assign_cell_id.py` builds a cell-id Cloud Optimized GeoTIFF (COG) for a single island and writes a copy of a points parquet file with a matching `cell_id` column.

The cell ids are assigned as follows:

- The boundary is reprojected to EPSG:4326.
- It is rasterized with `allTouched=True` and `targetAlignedPixels=True` at the given resolution (default 0.00025 degrees).
- Burned pixels are numbered 1..N in row-major order. 0 is nodata. The data type is uint32.

The parquet ids are read back from the written COG, so the raster and the parquet always agree.

## Installation

Pick one option.

### Linux (Debian/Ubuntu) and macOS (Homebrew)

```bash
./install.sh
source .venv/bin/activate
```

This installs system GDAL (`apt` or `brew`; apt needs sudo), creates `.venv`, and installs the packages. The Python GDAL bindings are installed at the same version as the system libgdal.

### Windows

Install [Miniforge](https://conda-forge.org/download/), then in PowerShell:

```powershell
.\install.ps1
conda activate islands_cell_id
```

### Any OS with conda

```bash
conda env create -f environment.yml
conda activate islands_cell_id
```

### Manual (pip)

Install GDAL (3.1 or newer, needed for the COG driver) on your system first, then:

```bash
pip install -r requirements.txt
```

The `GDAL==` version in `requirements.txt` must match your system libgdal (`gdal-config --version`).

## Usage

```bash
python assign_cell_id.py \
  --region island.geojson \
  --parquet points.parquet \
  --cell_id island_cell_id.tif \
  --output points_with_cell_id.parquet
```

| Argument | Required | Description |
|---|---|---|
| `--region` | yes | Boundary file (shapefile or GeoJSON) containing a single island |
| `--parquet` | yes | Input points parquet with `x` and `y` columns, without `cell_id` |
| `--cell_id` | yes | Output cell-id COG path |
| `--output` | yes | Output parquet path (input columns plus `cell_id`, uint32, gzip) |
| `--res` | no | Pixel size in degrees, default `0.00025` |

All paths are local files.

### Input expectations

- The boundary file holds one island. All of its features are rasterized together.
- The parquet has `x` (longitude) and `y` (latitude) columns at pixel centres, on the same grid this script produces.

### Errors

The script stops with an error instead of writing a wrong id when:

- a point lies outside the raster,
- a point is not at a pixel centre of the grid, or
- a point falls on a pixel outside the boundary (cell id 0).

## Files

| File | Purpose |
|---|---|
| `assign_cell_id.py` | Main script |
| `requirements.txt`, `environment.yml` | Dependencies (pip and conda) |
| `install.sh`, `install.ps1` | Installers (Linux/macOS and Windows) |
