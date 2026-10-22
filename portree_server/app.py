"""Portree NP: local E57 upload and conversion service (prototype)."""
from pathlib import Path
from uuid import uuid4
import secrets
import re
from fastapi import Request
import os
import subprocess
import sys
import threading
import traceback
import json
import numpy as np
from functools import lru_cache
from pydantic import BaseModel

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("PORTREE_DATA_DIR", str(ROOT / "portree_data"))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
CONVERTER = os.environ.get("POTREE_CONVERTER", "PotreeConverter")
MAX_BYTES = int(os.environ.get("PORTREE_MAX_UPLOAD_BYTES", str(20 * 1024**3)))
ADMIN_KEY = os.environ.get("PORTREE_ADMIN_KEY", "")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")

def require_admin(request: Request):
    if ADMIN_KEY:
        if not secrets.compare_digest(request.headers.get("X-Portree-Key", ""), ADMIN_KEY):
            raise HTTPException(403, "Chave administrativa inválida")
    elif request.client is None or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "Uploads exigem acesso local ou PORTREE_ADMIN_KEY")

def cloud_for_token(token):
    if not TOKEN_RE.fullmatch(token):
        raise HTTPException(404, "Compartilhamento não encontrado")
    for folder in (DATA / "clouds").iterdir():
        if folder.is_dir() and (folder / ".share-token").is_file():
            if secrets.compare_digest((folder / ".share-token").read_text().strip(), token):
                return folder
    raise HTTPException(404, "Compartilhamento não encontrado")
jobs = {}
lock = threading.Lock()
app = FastAPI(title="Portree NP E57 uploader")

def run_job(job_id, source, output):
    try:
        with lock:
            jobs[job_id]["status"] = "extracting"
        las_file = source.with_suffix(".las")
        subprocess.run([sys.executable, str(Path(__file__).with_name("e57_to_las.py")), str(source), str(las_file)], check=True)
        with lock:
            jobs[job_id]["status"] = "converting"
        output.mkdir(parents=True, exist_ok=True)
        subprocess.run([CONVERTER, str(las_file), "-o", str(output)], check=True)
        if not (output / "metadata.json").exists():
            raise RuntimeError("Converter finished without metadata.json")
        with lock:
            token = secrets.token_urlsafe(32)
            (output / ".share-token").write_text(token)
            jobs[job_id].update(status="ready", url=f"/s/{token}")
        las_file.unlink(missing_ok=True)
    except Exception as exc:
        with lock:
            jobs[job_id].update(status="error", error=str(exc))
        traceback.print_exc()

@app.post("/api/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    require_admin(request)
    if not file.filename or Path(file.filename).suffix.lower() != ".e57":
        raise HTTPException(400, "Selecione um arquivo .e57")
    job_id = uuid4().hex
    folder = DATA / "uploads" / job_id
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / "scan.e57"
    size = 0
    try:
        with source.open("wb") as dst:
            while chunk := await file.read(8 * 1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "Arquivo excede o limite configurado")
                dst.write(chunk)
    except Exception:
        source.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    with lock:
        jobs[job_id] = {"id": job_id, "status": "queued", "bytes": size}
    threading.Thread(target=run_job, args=(job_id, source, DATA / "clouds" / job_id), daemon=True).start()
    return jobs[job_id]

@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, request: Request):
    require_admin(request)
    with lock:
        if job_id not in jobs:
            raise HTTPException(404, "Processamento não encontrado")
        return dict(jobs[job_id])


class DepthQuery(BaseModel):
    station_id: int
    direction: list[float]

@lru_cache(maxsize=4)
def load_depth(path: str):
    with np.load(path) as data:
        return (data["depth"].copy(),data["origin"].copy(),data["rotation"].copy())

@app.post("/api/depth/{token}")
def query_depth(token: str, query: DepthQuery):
    folder=cloud_for_token(token)
    if len(query.direction)!=3 or not 1<=query.station_id<=9999:
        raise HTTPException(400,"Invalid direction or station")
    path=folder / f"depth_{query.station_id:03d}.npz"
    if not path.is_file():
        return {"status":"not_generated","message":"Depth map not generated for this station"}
    depth,origin,q=load_depth(str(path))
    direction=np.asarray(query.direction,dtype=np.float64)
    length=np.linalg.norm(direction)
    if not np.isfinite(length) or length<1e-9:
        raise HTTPException(400,"Invalid ray")
    direction/=length
    w,x,y,z=q
    R=np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
    local=direction@R
    az=np.arctan2(local[2],local[0])
    el=np.arcsin(np.clip(local[1],-1,1))
    h,width=depth.shape
    col=int(np.clip((az+np.pi)/(2*np.pi)*width,0,width-1))
    row=int(np.clip((np.pi/2-el)/np.pi*h,0,h-1))
    # Require several nearby samples to avoid claiming an isolated return is a surface.
    neighbors=[]
    for dy in range(-2,3):
        for dx in range(-2,3):
            rr=row+dy;cc=(col+dx)%width
            if 0<=rr<h and np.isfinite(depth[rr,cc]):
                neighbors.append(float(depth[rr,cc]))
    if len(neighbors)<4:
        return {"status":"no_depth","message":"Insufficient depth samples near click"}
    samples=np.asarray(neighbors)
    median=float(np.median(samples))
    spread=float(np.percentile(samples,90)-np.percentile(samples,10))
    if spread>max(0.20,median*0.025):
        return {"status":"ambiguous","message":"Depth discontinuity; cannot measure reliably"}
    xyz=(origin+direction*median).tolist()
    return {"status":"provisional","xyz":xyz,"range":median,"samples":len(samples),
            "warning":"Depth and panorama alignment have not been field-calibrated"}

@app.get("/s/{token}")
def shared_viewer(token: str):
    cloud_for_token(token)
    return FileResponse(Path(__file__).with_name("viewer.html"))

@app.get("/shared/{token}/{asset_path:path}")
def shared_asset(token: str, asset_path: str):
    folder = cloud_for_token(token)
    target = (folder / asset_path).resolve()
    if not target.is_relative_to(folder.resolve()) or not target.is_file() or target.name.startswith("."):
        raise HTTPException(404, "Arquivo não encontrado")
    return FileResponse(target)

@app.get("/portree/viewer.html")
def viewer_page():
    return FileResponse(Path(__file__).with_name("viewer.html"))

@app.get("/portree/")
def upload_page(request: Request):
    require_admin(request)
    return FileResponse(Path(__file__).with_name("index.html"))

(DATA / "clouds").mkdir(exist_ok=True)

app.mount("/potree-assets", StaticFiles(directory=str(ROOT)), name="potree-assets")
