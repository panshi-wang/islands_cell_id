import boto3
import os
import logging
from osgeo import gdal,ogr,osr
import geopandas as gpd
import pandas as pd
from datetime import datetime, timedelta
import numpy as np
import time
import tempfile
from ctypes import *

logger = logging.getLogger()
logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO)

def split_s3_path(s3_path):
	path_parts=s3_path.replace("s3://","").split("/")
	bucket=path_parts.pop(0)
	key="/".join(path_parts)
	return bucket, key

def s3_key_exists(client, bucket, key):
	response = client.list_objects_v2(Bucket=bucket,Prefix=key)
	return 'Contents' in response

def process_setup(region,filt,cell_id,res,buff,output):
	client = boto3.client('s3')
	with tempfile.TemporaryDirectory() as tmpdir:
		region_bucket,region_key = split_s3_path(region)
		client.download_file(region_bucket,region_key, f'{tmpdir}/region.shp')
		client.download_file(region_bucket,region_key.replace('.shp','.shx'), f'{tmpdir}/region.shx')
		client.download_file(region_bucket,region_key.replace('.shp','.dbf'), f'{tmpdir}/region.dbf')
		client.download_file(region_bucket,region_key.replace('.shp','.prj'), f'{tmpdir}/region.prj')
		region_gdf = gpd.GeoDataFrame.from_file(f'{tmpdir}/region.shp').to_crs(4326)
		if filt is not None:
			var,val = filt.split('=')
			if pd.api.types.is_integer_dtype(region_gdf[var]):
				val = int(val)
			elif pd.api.types.is_float_dtype(region_gdf[var]):
				val = float(val)
			region_gdf = region_gdf[region_gdf[var]==val]
		region_gdf.sindex
		if cell_id is not None:
			region_gdf.to_file(f'{tmpdir}/boundary.shp')
			region_ds = gdal.OpenEx(f'{tmpdir}/boundary.shp')
			gdal.Rasterize(f'{tmpdir}/boundary.tif', region_ds, format='GTIFF', outputType=gdal.GDT_Byte, noData=0, initValues=0, xRes=res, yRes=-1*res, allTouched=True, burnValues=1,targetAlignedPixels=True)
			boundary_ds = gdal.Open(f'{tmpdir}/boundary.tif')
			band = boundary_ds.GetRasterBand(1)
			aoi_arr = boundary_ds.ReadAsArray()
			xsize, ysize=boundary_ds.RasterXSize,boundary_ds.RasterYSize
			geotransform = boundary_ds.GetGeoTransform()
			proj = boundary_ds.GetProjection()
			aoi_ind = np.where(aoi_arr==1)
			id_arr = aoi_arr.astype(np.uint32).copy()
			id_arr[aoi_ind] = range(1,aoi_ind[0].size+1)
			id_arr[np.where(aoi_arr==0)] = 0
			driver = gdal.GetDriverByName("GTiff")
			outdata = driver.Create(f'{tmpdir}/cell_id.tif', xsize, ysize, 1, gdal.GDT_UInt32, options=['COMPRESS=LZW','TILED=YES','COPY_SRC_OVERVIEWS=YES'])
			outdata.SetGeoTransform(geotransform)
			outdata.SetProjection(proj)
			band = outdata.GetRasterBand(1)
			band.SetNoDataValue(0)
			band.WriteArray(id_arr)
			band.FlushCache()
			outdata = None
			band=None
			id_bucket,id_key = split_s3_path(cell_id)
			client.upload_file(f'{tmpdir}/cell_id.tif',id_bucket,id_key)
		region_buff = region_gdf.buffer(buff)
		output_file = os.path.basename(output)
		region_buff.to_file(f'{tmpdir}/{output_file}')
		output_bucket,output_key = split_s3_path(output)
		client.upload_file(f'{tmpdir}/{output_file}',output_bucket,output_key)
def main():
	import argparse
	parser = argparse.ArgumentParser()
	parser.add_argument('--region',type=str,required=True)
	parser.add_argument('--filt',type=str,required=True)
	parser.add_argument('--cell_id',type=str,required=False)
	parser.add_argument('--res',type=float,required=False)
	parser.add_argument('--buff',type=float,required=True)
	parser.add_argument('--output',type=str,required=True)
	args = parser.parse_args()
	process_setup(args.region,args.filt,args.cell_id,args.res,args.buff,args.output)
if __name__ == '__main__':
	main()