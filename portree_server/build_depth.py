"""Build a station-centric spherical depth index from a LAS point cloud.

Run once per station (start with station 1). All distances are in LAS coordinate
units; only interpret as metres after confirming the E57 coordinate units.
"""
import argparse
import json
from pathlib import Path
import numpy as np
import laspy

def build(las_path, stations_path, output, station_id=1, width=2048, height=1024, max_range=150.0):
    stations=json.loads(Path(stations_path).read_text(encoding="utf-8"))["stations"]
    station=next(s for s in stations if int(s["id"])==station_id)
    origin=np.asarray(station["position"],dtype=np.float64)
    # Quaternion in E57 order w,x,y,z. Rotate world vectors into station-local axes.
    w,x,y,z=map(float,station["rotation"])
    norm=np.linalg.norm([w,x,y,z])
    if not np.isfinite(norm) or norm<1e-12: raise ValueError("Invalid station quaternion")
    w,x,y,z=np.array([w,x,y,z])/norm
    R=np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
    # Keep nearest return per angular bin; empty bins remain NaN.
    depth=np.full(width*height,np.inf,dtype=np.float32)
    processed=0
    with laspy.open(str(las_path)) as reader:
        for chunk in reader.chunk_iterator(500_000):
            xyz=np.column_stack((chunk.x,chunk.y,chunk.z))
            local=(xyz-origin)@R
            dist=np.linalg.norm(local,axis=1)
            good=np.isfinite(dist)&(dist>0.1)&(dist<max_range)
            if not np.any(good):continue
            local=local[good];dist=dist[good]
            az=np.arctan2(local[:,2],local[:,0])
            el=np.arcsin(np.clip(local[:,1]/dist,-1,1))
            col=np.clip(((az+np.pi)/(2*np.pi)*width).astype(np.int64),0,width-1)
            row=np.clip(((np.pi/2-el)/np.pi*height).astype(np.int64),0,height-1)
            np.minimum.at(depth,row*width+col,dist.astype(np.float32))
            processed+=len(dist)
    depth[~np.isfinite(depth)]=np.nan
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    np.savez_compressed(output,depth=depth.reshape(height,width),origin=origin,rotation=np.array([w,x,y,z]),
                        width=np.array(width),height=np.array(height),max_range=np.array(max_range))
    coverage=float(np.isfinite(depth).mean())
    print(json.dumps({"station_id":station_id,"processed":processed,"coverage":coverage,
                      "output":str(output),"warning":"Validate coordinate units, quaternion and panorama alignment before engineering use."},indent=2))

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("las");p.add_argument("stations");p.add_argument("output")
    p.add_argument("--station",type=int,default=1)
    p.add_argument("--width",type=int,default=2048)
    p.add_argument("--height",type=int,default=1024)
    p.add_argument("--max-range",type=float,default=150)
    a=p.parse_args()
    build(a.las,a.stations,a.output,a.station,a.width,a.height,a.max_range)
