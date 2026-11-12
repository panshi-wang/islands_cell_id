"""Build a cell-id COG from an island boundary and assign cell ids to a parquet of points.

Cell-id logic is identical to boundary_buff.py:
  * boundary file holds a single island; reprojected to EPSG:4326
  * gdal.Rasterize(allTouched=True, targetAlignedPixels=True, xRes=res, yRes=-res)
  * ids = 1..N in row-major (C order) order over burned pixels, 0 = nodata, dtype uint32

Points (columns x=lon, y=lat, as written by export_parquet.py) are located at the
pixel centres of that raster; the ids are looked up from the written COG itself, so
the parquet and the GeoTIFF cannot disagree.
"""
import argparse
import logging
import os
import tempfile

import geopandas as gpd
import numpy as np
import pandas as pd
from osgeo import gdal

gdal.UseExceptions()
logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO)
logger = logging.getLogger()


def build_cell_id_array(region, res, tmpdir):
	region_gdf = gpd.read_file(region).to_crs(4326)
	if region_gdf.empty:
		raise ValueError('Boundary file contains no features')
	region_gdf.to_file(f'{tmpdir}/boundary.shp')
	region_ds = gdal.OpenEx(f'{tmpdir}/boundary.shp')
	gdal.Rasterize(f'{tmpdir}/boundary.tif', region_ds, format='GTIFF', outputType=gdal.GDT_Byte,
		noData=0, initValues=0, xRes=res, yRes=res, allTouched=True, burnValues=1,
		targetAlignedPixels=True)
	boundary_ds = gdal.Open(f'{tmpdir}/boundary.tif')
	aoi_arr = boundary_ds.ReadAsArray()
	geotransform = boundary_ds.GetGeoTransform()
	proj = boundary_ds.GetProjection()
	aoi_ind = np.where(aoi_arr == 1)
	id_arr = aoi_arr.astype(np.uint32).copy()
	id_arr[aoi_ind] = range(1, aoi_ind[0].size+1)
	id_arr[np.where(aoi_arr == 0)] = 0
	return id_arr, geotransform, proj


def write_cog(id_arr, geotransform, proj, path, tmpdir):
	driver = gdal.GetDriverByName('GTiff')
	ysize, xsize = id_arr.shape
	tmp_path = f'{tmpdir}/cell_id.tif'
	ds = driver.Create(tmp_path, xsize, ysize, 1, gdal.GDT_UInt32, options=['COMPRESS=LZW', 'TILED=YES'])
	ds.SetGeoTransform(geotransform)
	ds.SetProjection(proj)
	band = ds.GetRasterBand(1)
	band.SetNoDataValue(0)
	band.WriteArray(id_arr)
	band.FlushCache()
	band = None
	ds = None
	# Lossless COG (LZW), no resampling of values; overviews use nearest so ids stay valid
	gdal.GetDriverByName('COG').CreateCopy(path, gdal.Open(tmp_path),
		options=['COMPRESS=LZW', 'OVERVIEW_RESAMPLING=NEAREST', 'BLOCKSIZE=512'])


def assign_points(cog_path, parquet_in, parquet_out):
	ds = gdal.Open(cog_path)
	gt = ds.GetGeoTransform()
	xsize, ysize = ds.RasterXSize, ds.RasterYSize
	arr = ds.GetRasterBand(1).ReadAsArray()
	df = pd.read_parquet(parquet_in)
	x = df['x'].to_numpy(dtype=np.float64)
	y = df['y'].to_numpy(dtype=np.float64)
	# Points sit at pixel centres, so subtracting 0.5 and rounding is robust to float noise
	col = np.rint((x-gt[0])/gt[1]-0.5).astype(np.int64)
	row = np.rint((y-gt[3])/gt[5]-0.5).astype(np.int64)
	inside = (col >= 0) & (col < xsize) & (row >= 0) & (row < ysize)
	if not inside.all():
		raise ValueError(f'{(~inside).sum()} points fall outside the cell-id raster')
	# Verify each point really is at its pixel centre (tolerance 1e-3 pixel)
	cx = gt[0]+(col+0.5)*gt[1]
	cy = gt[3]+(row+0.5)*gt[5]
	if (np.abs(cx-x) > 1e-3*abs(gt[1])).any() or (np.abs(cy-y) > 1e-3*abs(gt[5])).any():
		raise ValueError('Some points are not at pixel centres of the cell-id grid')
	ids = arr[row, col]
	if (ids == 0).any():
		raise ValueError(f'{(ids == 0).sum()} points fall on pixels outside the boundary (cell_id 0)')
	df['cell_id'] = ids.astype(np.uint32)
	df.to_parquet(parquet_out, compression='gzip')
	logger.info(f'Assigned cell_id to {len(df)} points -> {parquet_out}')


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument('--region', required=True, help='Boundary shapefile or geojson')
	parser.add_argument('--parquet', required=True, help='Input parquet from export_parquet.py')
	parser.add_argument('--res', type=float, default=0.00025)
	parser.add_argument('--cell_id', required=True, help='Output cell-id COG path')
	parser.add_argument('--output', required=True, help='Output parquet path')
	args = parser.parse_args()
	with tempfile.TemporaryDirectory() as tmpdir:
		id_arr, gt, proj = build_cell_id_array(args.region, args.res, tmpdir)
		logger.info(f'Raster {id_arr.shape[1]}x{id_arr.shape[0]}, {int((id_arr > 0).sum())} cells')
		write_cog(id_arr, gt, proj, args.cell_id, tmpdir)
	assign_points(args.cell_id, args.parquet, args.output)


if __name__ == '__main__':
	main()
