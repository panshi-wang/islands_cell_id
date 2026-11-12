# Windows installer: creates the "islands_cell_id" conda environment from environment.yml.
# Usage (PowerShell):  .\install.ps1
# Requires conda, mamba or micromamba (e.g. Miniforge: https://conda-forge.org/download/).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$tool = $null
foreach ($candidate in "mamba", "micromamba", "conda") {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) { $tool = $candidate; break }
}
if (-not $tool) {
    Write-Error "conda/mamba not found. Install Miniforge first, then re-run."
    exit 1
}

& $tool env create -y -f environment.yml
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $tool run -n islands_cell_id python -c "from osgeo import gdal; assert gdal.GetDriverByName('COG'), 'COG driver missing'; print('GDAL', gdal.__version__, '- OK')"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Done. Activate with: $tool activate islands_cell_id"
