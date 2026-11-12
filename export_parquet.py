import boto3
import os
import logging
from osgeo import gdal,ogr,osr
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from datetime import datetime, timedelta
import numpy as np
import time
import tempfile
from ctypes import *
import pyarrow as pa

logger = logging.getLogger()
logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO)
logger.info(pd.__version__)
logger.info(pa.__version__)
def split_s3_path(s3_path):
	path_parts=s3_path.replace("s3://","").split("/")
	bucket=path_parts.pop(0)
	key="/".join(path_parts)
	return bucket, key

def s3_key_exists(client, bucket, key):
	response = client.list_objects_v2(Bucket=bucket,Prefix=key)
	return 'Contents' in response

def composite_setup(region,filt,island,id,ys,ye,output):
	client = boto3.client('s3')
	with tempfile.TemporaryDirectory() as tmpdir:
		region_bucket,region_key = split_s3_path(region)
		client.download_file(region_bucket,region_key, f'{tmpdir}/region.shp')
		client.download_file(region_bucket,region_key.replace('.shp','.shx'), f'{tmpdir}/region.shx')
		client.download_file(region_bucket,region_key.replace('.shp','.dbf'), f'{tmpdir}/region.dbf')
		client.download_file(region_bucket,region_key.replace('.shp','.prj'), f'{tmpdir}/region.prj')
		region_gdf = gpd.GeoDataFrame.from_file(f'{tmpdir}/region.shp').to_crs(4326)
		var,val = filt.split('=')
		if pd.api.types.is_integer_dtype(region_gdf[var]):
			val = int(val)
		elif pd.api.types.is_float_dtype(region_gdf[var]):
			val = float(val)
		region_gdf = region_gdf[region_gdf[var]==val]
		region_gdf.sindex
		utm_crs = region_gdf.estimate_utm_crs()
		region_gdf_utm = region_gdf.to_crs(utm_crs)

		ds = gdal.Open(f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/Outline/{island}_{id}_cell_id.tif')
		xsize, ysize=ds.RasterXSize,ds.RasterYSize
		geotransform = ds.GetGeoTransform()
		proj = ds.GetProjection()
		if ds.GetMetadata_Dict().get('AREA_OR_POINT') == 'Area':
			x_offset = 0.5*geotransform[1]
			y_offset = 0.5*geotransform[5]
		else:
			x_offset = 0
			y_offset = 0
		yl = ye-ys+1
		band = ds.GetRasterBand(1)
		id_arr = band.ReadAsArray()
		ys_aoi,xs_aoi = np.where(id_arr>0)
		id_aoi = id_arr[np.where(id_arr>0)]
		N_aoi = len(ys_aoi)
		lons_aoi = np.array([geotransform[0] + x *geotransform[1]+x_offset for x in xs_aoi])
		lats_aoi = np.array([geotransform[3] + y *geotransform[5]+y_offset for y in ys_aoi])
		points_aoi = gpd.GeoSeries([Point(lons_aoi[i],lats_aoi[i]) for i in range(N_aoi)], crs="EPSG:4326")
		points_aoi_utm = points_aoi.to_crs(utm_crs)
		distance = [region_gdf_utm.geometry.boundary.distance(points_aoi_utm[i]).min() for i in range(N_aoi)]
		output_df = pd.DataFrame({'cell_id': np.tile(id_aoi,yl),'x': np.tile(lons_aoi,yl), 'y': np.tile(lats_aoi,yl),'dist2bound': np.tile(distance,yl), 'year': np.repeat(range(ys,ye+1),N_aoi)})
		
		static_vars = {'aspect':f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/ext/{island}_{id}_aspect.tif',
		'dem':f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/ext/{island}_{id}_dem.tif',
		'slope':f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/ext/{island}_{id}_slope.tif',
		'twi':f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/ext/{island}_{id}_twi.tif',
		'wc2021':f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/ext/{island}_{id}_worldcover.tif',
		}
		static_nodata = {'aspect':-9999,'dem':-32768,'slope':-9999,'twi':np.nan,'wc2021':0}
		static_types = {'aspect':'float32','dem':'int16','slope':'float32','twi':'float32','wc2021':'uint8'}
		for layer in static_vars:
			try:
				ds = gdal.Open(static_vars[layer])
				band = ds.GetRasterBand(1)
				arr = band.ReadAsArray()
				xsize, ysize=ds.RasterXSize,ds.RasterYSize
				geotransform = ds.GetGeoTransform()
				xs_aoi = [int((lon-geotransform[0]-x_offset)/geotransform[1]+0.00001) for lon in lons_aoi]
				ys_aoi = [int((lat-geotransform[3]-y_offset)/geotransform[5]+0.00001) for lat in lats_aoi]
				valid = [xs_aoi[i]>=0 and xs_aoi[i]<xsize and ys_aoi[i]>=0 and ys_aoi[i]<ysize for i in range(N_aoi)]
				vals_aoi = np.array([arr[ys_aoi[i],xs_aoi[i]] if valid[i] else np.nan for i in range(N_aoi)])
				vals_aoi = np.where(vals_aoi==static_nodata[layer],np.nan,vals_aoi)
				output_df[layer] = np.tile(vals_aoi,yl)
			except Exception as err:
				logger.info(f'Loading File error: {layer},{err}')
				output_df[layer] = np.tile([np.nan for i in range(N_aoi)],yl)
			output_df[layer] = output_df[layer].astype('float32')

		layers = ['ndvi_mean','ndvi_std','ndvi_count','ndvi_p2','ndvi_p25','ndvi_p50','ndvi_p75','ndvi_p98',
		'ndmi_mean','ndmi_std','ndmi_count','ndmi_p2','ndmi_p25','ndmi_p50','ndmi_p75','ndmi_p98',
		'evi_mean','evi_std','evi_count','evi_p2','evi_p25','evi_p50','evi_p75','evi_p98',
		'msavi2_mean','msavi2_std','msavi2_count','msavi2_p2','msavi2_p25','msavi2_p50','msavi2_p75','msavi2_p98',
		'tc_cnt_v2.3','tc_err_v2.3','tc_p50_v2.3','biomass_v1']
		scaler = {'ndvi_mean': 0.001,'ndvi_std': 0.001,'ndvi_count': 1,'ndvi_p2': 0.001,'ndvi_p25': 0.001,'ndvi_p50': 0.001,'ndvi_p75':  0.001,'ndvi_p98': 0.001,
		'ndmi_mean': 0.001,'ndmi_std':  0.001,'ndmi_count': 1,'ndmi_p2': 0.001,'ndmi_p25': 0.001,'ndmi_p50': 0.001,'ndmi_p75': 0.001,'ndmi_p98': 0.001,
		'evi_mean': 0.001,'evi_std': 0.001,'evi_count': 1,'evi_p2': 0.001,'evi_p25': 0.001,'evi_p50': 0.001,'evi_p75': 0.001,'evi_p98': 0.001,
		'msavi2_mean': 0.001,'msavi2_std': 0.001,'msavi2_count': 1,'msavi2_p2': 0.001,'msavi2_p25': 0.001,'msavi2_p50': 0.001,'msavi2_p75': 0.001,'msavi2_p98': 0.001,
		'tc_cnt_v2.0': 1,'tc_err_v2.0': 1,'tc_p50_v2.0': 1,'tc_cnt_v2.2': 1,'tc_err_v2.2': 1,'tc_p50_v2.2': 1,'tc_cnt_v2.3': 1,'tc_err_v2.3': 1,'tc_p50_v2.3': 1,'biomass_v1':1}
		nodata = {'ndvi_mean': -9999,'ndvi_std': -9999,'ndvi_count': 99999,'ndvi_p2': -9999,'ndvi_p25': -9999,'ndvi_p50': -9999,'ndvi_p75':  -9999,'ndvi_p98': -9999,
		'ndmi_mean': -9999,'ndmi_std':  -9999,'ndmi_count': 99999,'ndmi_p2': -9999,'ndmi_p25': -9999,'ndmi_p50': -9999,'ndmi_p75': -9999,'ndmi_p98': -9999,
		'evi_mean': -9999,'evi_std': -9999,'evi_count': 99999,'evi_p2': -9999,'evi_p25': -9999,'evi_p50': -9999,'evi_p75': -9999,'evi_p98': -9999,
		'msavi2_mean': -9999,'msavi2_std': -9999,'msavi2_count': 99999,'msavi2_p2': -9999,'msavi2_p25': -9999,'msavi2_p50': -9999,'msavi2_p75': -9999,'msavi2_p98': -9999,
		'tc_cnt_v2.0': -9999,'tc_err_v2.0': -9999,'tc_p50_v2.0': 255,'tc_cnt_v2.2': -9999,'tc_err_v2.2': -9999,'tc_p50_v2.2': 255,'tc_cnt_v2.3': -9999,'tc_err_v2.3': -9999,'tc_p50_v2.3': 255,'biomass_v1':-99999}
		for layer in layers:
			layer_vals = []
			for year in range(ys,ye+1):
				var_files = {'ndvi_mean': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_ndvi_mean.tif',
				'ndvi_std': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_ndvi_std.tif',
				'ndvi_count': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_ndvi_count.tif',
				'ndvi_p2': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_ndvi_p2.tif',
				'ndvi_p25': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_ndvi_p25.tif',
				'ndvi_p50': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_ndvi_p50.tif',
				'ndvi_p75': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_ndvi_p75.tif',
				'ndvi_p98': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_ndvi_p98.tif',
				'ndmi_mean': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_ndmi_mean.tif',
				'ndmi_std': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_ndmi_std.tif',
				'ndmi_count': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_ndmi_count.tif',
				'ndmi_p2': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_ndmi_p2.tif',
				'ndmi_p25': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_ndmi_p25.tif',
				'ndmi_p50': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_ndmi_p50.tif',
				'ndmi_p75': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_ndmi_p75.tif',
				'ndmi_p98': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_ndmi_p98.tif',
				'evi_mean': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_evi_mean.tif',
				'evi_std': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_evi_std.tif',
				'evi_count': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_evi_count.tif',
				'evi_p2': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_evi_p2.tif',
				'evi_p25': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_evi_p25.tif',
				'evi_p50': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_evi_p50.tif',
				'evi_p75': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_evi_p75.tif',
				'evi_p98': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_evi_p98.tif',
				'msavi2_mean': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_msavi2_mean.tif',
				'msavi2_std': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_msavi2_std.tif',
				'msavi2_count': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_msavi2_count.tif',
				'msavi2_p2': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_msavi2_p2.tif',
				'msavi2_p25': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_msavi2_p25.tif',
				'msavi2_p50': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_msavi2_p50.tif',
				'msavi2_p75': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_msavi2_p75.tif',
				'msavi2_p98': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/si_v2.2/{year}_{island}_{id}_msavi2_p98.tif',
				'tc_cnt_v2.0': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/tc_v2.0/{year}_{island}_{id}_tc_cnt.tif',
				'tc_err_v2.0': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/tc_v2.0/{year}_{island}_{id}_tc_err.tif',
				'tc_p50_v2.0': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/tc_v2.0/{year}_{island}_{id}_tc_p50.tif',
				'tc_cnt_v2.2': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/tc_v2.2/{year}_{island}_{id}_tc_cnt.tif',
				'tc_err_v2.2': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/tc_v2.2/{year}_{island}_{id}_tc_err.tif',
				'tc_p50_v2.2': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/tc_v2.2/{year}_{island}_{id}_tc_p50.tif',
				'tc_cnt_v2.3': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/tc_v2.3/{year}_{island}_{id}_tc_cnt.tif',
				'tc_err_v2.3': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/tc_v2.3/{year}_{island}_{id}_tc_err.tif',
				'tc_p50_v2.3': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/tc_v2.3/{year}_{island}_{id}_tc_p50.tif',
				'biomass_v1': f'/vsis3/terrapulse-userspace/islandconserv/NASA_project/{island}_{id}/biomass_v1/{year}_{island}_{id}_biomass.tif'
				}
				if s3_key_exists(client, 'terrapulse-userspace',var_files[layer].replace('/vsis3/terrapulse-userspace/','')):
					try:
						logger.info(f'Loading File: {var_files[layer]}')
						ds = gdal.Open(var_files[layer])
						band = ds.GetRasterBand(1)
						arr = band.ReadAsArray()
						xsize, ysize=ds.RasterXSize,ds.RasterYSize
						geotransform = ds.GetGeoTransform()
						logger.info(f'Loaded File: {var_files[layer]}')
						xs_aoi = [int((lon-geotransform[0]-x_offset)/geotransform[1]+0.00001) for lon in lons_aoi]
						ys_aoi = [int((lat-geotransform[3]-y_offset)/geotransform[5]+0.00001) for lat in lats_aoi]
						valid = [xs_aoi[i]>=0 and xs_aoi[i]<xsize and ys_aoi[i]>=0 and ys_aoi[i]<ysize for i in range(N_aoi)]
						logger.info(f'Valid Index: {var_files[layer]}')
						vals_aoi = np.array([arr[ys_aoi[i],xs_aoi[i]] if valid[i] else np.nan for i in range(N_aoi)])
						vals_aoi = np.where(vals_aoi==nodata[layer],np.nan,vals_aoi)
						vals_aoi = scaler[layer]*vals_aoi
						layer_vals.extend(list(vals_aoi))
					except Exception as err:
						logger.info(f'Loading File error: {var_files[layer]},{err}')
						layer_vals.extend([np.nan for i in range(N_aoi)])
				else:
					logger.info(f'File does not exist: {var_files[layer]}')
					layer_vals.extend([np.nan for i in range(N_aoi)])
			output_df[layer] = np.array(layer_vals)
			output_df[layer] = output_df[layer].astype('float32')
			# output_df[layer] = output_df[layer].astype(types[layer])
		output_df.to_parquet(f'{tmpdir}/output.parquet', compression='gzip')
		output_bucket,output_key = split_s3_path(output)
		client.upload_file(f'{tmpdir}/output.parquet',output_bucket,output_key)
def main():
	import argparse
	parser = argparse.ArgumentParser()
	parser.add_argument('--region',type=str,required=True)
	parser.add_argument('--filt',type=str,required=True)
	parser.add_argument('--island',type=str,required=True)
	parser.add_argument('--id',type=str,required=True)
	parser.add_argument('--ys',type=int,required=True)
	parser.add_argument('--ye',type=int,required=True)
	parser.add_argument('--output',type=str,required=True)
	args = parser.parse_args()
	composite_setup(args.region,args.filt,args.island,args.id,args.ys,args.ye,args.output)
if __name__ == '__main__':
	main()