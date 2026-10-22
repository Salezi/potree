# Portree NP — E57 Web Upload (prototype)

This is an initial **geometry-only** E57 ingestion prototype. It does **not** yet extract FARO panoramic images, station poses, depth maps or BubbleViews. It is **not** production-ready or safe to expose publicly: there is no authentication, persistence for job status, upload quotas per user, or secure job queue.

## Windows quick start

Install Python 3.11/3.12 and Node.js. From the repository root:

```powershell
npm install
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r portree_server/requirements.txt
$env:POTREE_CONVERTER="C:\path\to\PotreeConverter.exe"
uvicorn portree_server.app:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/portree/ and upload an E57. The converter binary must be compiled or downloaded separately. Potree viewer assets must exist under build/potree (run npm build). Data is stored in portree_data/, which should be excluded from Git and backups as appropriate.

## Known limitations

- `pye57.read_scan` reads an entire station into memory; large scans may exhaust RAM.
- Panoramic images and station transforms are not preserved by this LAS conversion.
- Job state is in memory and is lost on restart.
- Only use locally or on a trusted network until authentication, storage controls, background workers and rate limits are implemented.
- Validate accuracy, offsets and color scaling against a real FARO E57 before engineering use.
