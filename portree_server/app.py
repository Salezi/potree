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
