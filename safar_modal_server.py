"""SAFAR STUDIO PRO v5.0. Deploy from this project's root with Modal 1.6."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from uuid import UUID, uuid4

import modal

ROOT=Path(__file__).resolve().parent
APP="safar-studio-pro"
app=modal.App(APP)
weights=modal.Volume.from_name("safar-studio-pro-weights",create_if_missing=True)
data=modal.Volume.from_name("safar-studio-pro-audio",create_if_missing=True)
states=modal.Dict.from_name("safar-studio-pro-jobs",create_if_missing=True)
auth=modal.Secret.from_name("safar-studio-pro-auth",required_keys=["SAFAR_GPU_TOKEN"])
mounts={"/weights":weights,"/data":data}
ACE_REV="ca1e85fe9430179831e6bc6be790c332190a3866"
api_image=(modal.Image.debian_slim(python_version="3.11").apt_install("libsndfile1")
           .pip_install("fastapi==0.121.3","pydantic>=2.11,<3","soundfile==0.13.1")
           .add_local_dir(ROOT/"engine",remote_path="/root/engine"))
gpu_image=(modal.Image.debian_slim(python_version="3.11")
    .apt_install("git","ca-certificates","curl","build-essential","pkg-config","nasm","libsndfile1","libmp3lame-dev","libgomp1")
    .pip_install("uv==0.9.13")
    .run_commands("git clone -q --depth 1 -b n7.1.3 https://github.com/FFmpeg/FFmpeg.git /tmp/ffmpeg && cd /tmp/ffmpeg && ./configure --disable-gpl --disable-nonfree --disable-doc --enable-shared --disable-static --enable-libmp3lame && make -j6 && make install && ldconfig && rm -rf /tmp/ffmpeg",
                  f"git clone -q https://github.com/ace-step/ACE-Step-1.5.git /opt/ace && cd /opt/ace && git checkout -q {ACE_REV} && uv sync --frozen --no-dev --python 3.11",
                  "uv pip install --python /opt/ace/.venv/bin/python pyloudnorm==0.2.0 faster-whisper==1.2.1 BS-RoFormer==1.4.1",
                  "uv venv /opt/demucs --python 3.11 && uv pip install --python /opt/demucs/bin/python --index-url https://download.pytorch.org/whl/cu118 torch==2.0.1+cu118 torchaudio==2.0.2+cu118",
                  "uv pip install --python /opt/demucs/bin/python 'numpy<2' demucs==4.0.1 soundfile==0.13.1 pyloudnorm==0.2.0",
                  "/opt/ace/.venv/bin/python -c 'from acestep.handler import AceStepHandler; from bs_roformer import BSRoformer, MelBandRoformer; from faster_whisper import WhisperModel; import pyloudnorm, torch; assert torch.version.cuda is not None'",
                  "/opt/demucs/bin/python -c 'import demucs.pretrained, torchaudio; assert torchaudio.__version__.startswith(\"2.0.2\")'")
    .env({"ACESTEP_CHECKPOINTS_DIR":"/weights/checkpoints","HF_HOME":"/weights/hf","TORCH_HOME":"/weights/torch",
          "LD_LIBRARY_PATH":"/opt/ace/.venv/lib/python3.11/site-packages/nvidia/cudnn/lib:/opt/ace/.venv/lib/python3.11/site-packages/nvidia/cublas/lib:/usr/local/nvidia/lib64:/usr/local/lib",
          "PYTHONPATH":"/root:/opt/ace","PYTHONUNBUFFERED":"1"})
    .add_local_dir(ROOT/"engine",remote_path="/root/engine"))
VENVPY="/opt/ace/.venv/bin/python"
FILES={"Master.wav","Master.mp3","SyncPack.zip","Stems.zip","words.json","Instrumental.wav","TV_Mix.wav","Clean.wav"}
STEMS=["vocals","drums","bass","guitar","piano","other"]
FILES.update(s+ext for s in STEMS for ext in (".wav","-preview.mp3"))


@app.function(image=gpu_image,volumes=mounts,timeout=10800, max_containers=1)
def prepare_models():
    subprocess.run([VENVPY,"-c","from engine.models import prepare; prepare()"],check=True)
    weights.commit()
    return "Pinned weights ready"


@app.function(image=api_image,secrets=[auth])
def deployment_token():
    # Workspace-authenticated Modal RPC, used by the owner's Colab; no HTTP route.
    return os.environ["SAFAR_GPU_TOKEN"]


def folder(owner,id):return Path("/data/jobs")/str(owner)/str(id)


def state(id):
    row=states.get(str(id))
    if row and (row["state"] in ("done","failed") or time.time()-row["created"]<=15000):return row
    data.reload()
    index=Path("/data/index")/(str(id)+".json")
    if index.is_file():
        archived=json.loads(index.read_text())
        if archived["state"] in ("done","failed") or not row:return archived
    if row and time.time()-row["created"]>15000:
        return {**row,"state":"failed","error":"Worker deadline exceeded"}
    return row


def save_final(id,row):
    dest=Path("/data/index")/(id+".json");dest.parent.mkdir(parents=True,exist_ok=True)
    temp=dest.with_suffix(".partial");temp.write_text(json.dumps(row,allow_nan=False));temp.replace(dest)
    data.commit();states[id]=row


def execute(job,hardware):
    id=job["id"];key="exec/"+id
    if not states.put(key,True,skip_if_exists=True):return
    started=time.time();row=states[id];data.reload();weights.reload()
    dest=folder(job["owner"],id);dest.mkdir(parents=True,exist_ok=True)
    try:
        with tempfile.TemporaryDirectory() as local:
            f=Path(local)/"job.json";f.write_text(json.dumps(job))
            with subprocess.Popen([VENVPY,"-m","engine.worker",str(f)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors="replace") as proc:
                for line in proc.stdout:
                    if line.startswith("SAFAR_EVENT "):
                        row.update(state="running",**json.loads(line[12:]));states[id]=row
                    else:print(line.rstrip(),flush=True)
                if proc.wait()!=0:raise RuntimeError("GPU stage failed: "+str(row.get("stage","startup")))
        result=json.loads((dest/"result.json").read_text())
        row.update(state="done",stage="Complete",result=result)
    except Exception as exc:
        row.update(state="failed",stage="Failed",error=str(exc)[:260])
    row.update(gpu=hardware,gpu_seconds=round(time.time()-started,3),finished=time.time())
    save_final(id,row)
    if job.get("completion_url") and job.get("completion_mac"):
        import urllib.request
        notice=urllib.request.Request(job["completion_url"],data=json.dumps({"owner":job["owner"],"id":id}).encode(),
                headers={"Content-Type":"application/json","X-Safar-Notify":job["completion_mac"]})
        try:
            with urllib.request.urlopen(notice,timeout=12) as response:response.read(128)
        except OSError as exc:
            print("Completion notification deferred to reconciliation:",type(exc).__name__)


@app.function(image=gpu_image,gpu="L4",memory=32768,volumes=mounts,timeout=14400,max_containers=4,scaledown_window=2)
def sketch(job):execute(job,"L4")


@app.function(image=gpu_image,gpu="L40S",memory=65536,volumes=mounts,timeout=14400,max_containers=3,scaledown_window=2)
def studio(job):execute(job,"L40S")


@app.function(image=gpu_image,gpu="L40S",memory=65536,volumes=mounts,timeout=14400,max_containers=4,scaledown_window=2)
def priority(job):execute(job,"L40S")


def dispatch(req,kind):
    req.check_task(kind)
    payload=req.model_dump(mode="json");id=str(req.id or uuid4());payload.update(id=id,kind=kind)
    fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    row={"id":id,"owner":str(req.owner),"state":"queued","stage":"Queued","created":time.time(),"fingerprint":fingerprint}
    existing=state(id)
    fresh=not existing and states.put(id,row,skip_if_exists=True)
    if not fresh:
        existing=existing or states[id]
        if existing["fingerprint"]!=fingerprint:raise ValueError("Job ID already used for different inputs")
        if existing["state"]!="queued" or time.time()-existing["created"]<90:return id
    lane=priority if req.priority else studio if req.model=="studio" or kind=="train" or req.duration_sec>180 else sketch
    lane.spawn(payload)
    return id


def sign(claim):
    raw=base64.urlsafe_b64encode(json.dumps(claim,separators=(",",":")).encode()).decode().rstrip("=")
    sig=hmac.new(os.environ["SAFAR_GPU_TOKEN"].encode(),raw.encode(),"sha256").hexdigest()
    return raw+"."+sig


def decode_ticket(ticket):
    raw,sig=ticket.split(".",1)
    key=os.environ["SAFAR_GPU_TOKEN"].encode()
    if not hmac.compare_digest(hmac.new(key,raw.encode(),"sha256").hexdigest(),sig):raise ValueError("Bad signature")
    obj=json.loads(base64.urlsafe_b64decode(raw+"==="))
    if time.time()>obj["exp"]:raise ValueError("Expired")
    return obj


@app.function(image=api_image,secrets=[auth],volumes={"/data":data},timeout=15000,memory=4096,scaledown_window=30)
@modal.asgi_app()
def api():
    import soundfile as sf
    from fastapi import FastAPI,Header,HTTPException,Request,Depends
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse,JSONResponse,Response
    from engine.contracts import Job,Ticket
    web=FastAPI(title="SAFAR STUDIO",version="5.0.0")
    web.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["GET","PUT","POST","OPTIONS"],allow_headers=["Authorization","Content-Type","Prefer","Range"])
    def authorized(authorization:str=Header("")):
        if not hmac.compare_digest(authorization,"Bearer "+os.environ["SAFAR_GPU_TOKEN"]):raise HTTPException(401,"Invalid bearer")
    protected=[Depends(authorized)]
    @web.get("/health",dependencies=protected)
    def health():
        return {"status":"ok","version":"5.0.0","service":"SAFAR STUDIO Modal GPU","gpu":"L4 / L40S","models":{"ace_step_1_5":"lazy-loaded","demucs":"licensed-weights-only","pyloudnorm":"installed","faster_whisper":"large-v3-turbo"}}
    def submit(req,kind,wait):
        try:id=dispatch(req,kind)
        except ValueError as exc:raise HTTPException(422,str(exc))
        if wait:
            until=time.monotonic()+14400
            while time.monotonic()<until:
                r=state(id)
                if r["state"]=="failed":raise HTTPException(503,r.get("error"))
                if r["state"]=="done":
                    data.reload();d=folder(req.owner,id)
                    def b64(name):return base64.b64encode((d/name).read_bytes()).decode()
                    if kind=="repaint":return {**r["result"],"status":"neural_repaint_bit_exact","master_wav_b64":b64("Master.wav"),"job_id":id}
                    res=r["result"];aliases={"vocals":"lead","other":"chords","bass":"bass","drums":"drums"}
                    return {**res,**res["telemetry"],"status":"neural_gpu_verified","master_wav_b64":b64("Master.wav"),"stems_b64":{v:b64(k+".wav") for k,v in aliases.items() if (d/(k+".wav")).exists()},"elapsed_sec":r["gpu_seconds"],"job_id":id}
                time.sleep(2)
            raise HTTPException(504,"GPU deadline exceeded; job "+id)
        return JSONResponse({"id":id,"job_id":id,"status_url":"/api/jobs/"+id},202,headers={"Location":"/api/jobs/"+id})
    @web.post("/api/generate",dependencies=protected)
    def generate(req:Job,prefer:str=Header("")):return submit(req,"generate","respond-async" not in prefer)
    @web.post("/api/repaint",dependencies=protected)
    def repaint(req:Job,prefer:str=Header("")):return submit(req,"repaint","respond-async" not in prefer)
    @web.post("/api/lora/train",dependencies=protected)
    def train(req:Job):return submit(req,"train",False)
    @web.post("/api/jobs",dependencies=protected)
    def create(req:Job):return submit(req,req.kind,False)
    @web.get("/api/jobs/{id}",dependencies=protected)
    def job(id:UUID):
        r=state(str(id))
        if not r:raise HTTPException(404,"Job missing")
        return {k:v for k,v in r.items() if k!="fingerprint"}
    @web.post("/api/export/sync-pack",dependencies=protected)
    def pack(t:Ticket):
        data.reload();p=folder(t.owner,t.job_id)/"SyncPack.zip"
        if not p.is_file():raise HTTPException(404,"Render six stems first")
        return FileResponse(p,filename="SAFAR_Sync_Pack.zip",media_type="application/zip")
    @web.post("/api/tickets",dependencies=protected)
    def tickets(t:Ticket):
        if t.op=="read" and (not t.job_id or t.file not in FILES):raise HTTPException(422,"Unknown output")
        id=str(t.job_id or uuid4());claim={"op":t.op,"owner":str(t.owner),"id":id,"file":t.file,"exp":int(time.time())+1800}
        return {"id":id,"path":"/media/"+sign(claim),"expires_at":claim["exp"]}
    def claim(token,op):
        try:c=decode_ticket(token)
        except (ValueError,TypeError,KeyError):raise HTTPException(403,"Expired or invalid file ticket")
        if c["op"]!=op:raise HTTPException(403,"Invalid ticket purpose")
        return c
    @web.get("/media/{token}")
    def read(token:str):
        c=claim(token,"read");data.reload();p=folder(c["owner"],c["id"])/c["file"]
        if not p.is_file():raise HTTPException(404,"File not available")
        return FileResponse(p,filename=p.name,headers={"Cache-Control":"private, no-store"})
    @web.put("/media/{token}")
    async def upload(token:str,request:Request):
        c=claim(token,"upload");p=Path("/data/uploads")/c["owner"]/c["id"];p.parent.mkdir(parents=True,exist_ok=True)
        size=0
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/"audio"
            with src.open("wb") as f:
                async for chunk in request.stream():
                    size+=len(chunk)
                    if size>180*1024**2:raise HTTPException(413,"Maximum 180 MiB")
                    f.write(chunk)
            try:info=sf.info(src)
            except Exception:raise HTTPException(422,"Audio decode failed")
            if not (.08<=info.duration<=600 and info.channels in (1,2) and 8000<=info.samplerate<=192000):raise HTTPException(422,"Use 0.08–600 s mono/stereo audio")
            import shutil
            data.reload()
            if p.exists():raise HTTPException(409,"Upload already committed")
            shutil.copyfile(src,p);data.commit()
        return {"id":c["id"],"duration":info.duration}
    return web
