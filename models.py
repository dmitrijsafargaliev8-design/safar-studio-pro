"""ACE-Step and licensed source-separation adapters. Inputs are trusted local paths."""
import gc
import json
import os
from pathlib import Path
from typing import Protocol

import numpy as np
import soundfile as sf

from .dsp import STEMS, align, digest

ROOT=Path("/opt/ace")
CACHE=Path("/weights/checkpoints")
MANIFEST=json.loads(Path(__file__).with_name("models.json").read_text())
VARIANTS={"sketch":"acestep-v15-turbo","studio":"acestep-v15-xl-sft","extract":"acestep-v15-base"}
ASR="dropbox-dash/faster-whisper-large-v3-turbo"


def prepare():
    from huggingface_hub import snapshot_download
    for repo,item in MANIFEST.items():
        if repo.startswith("Qwen/"): dest=Path("/weights/review")
        elif repo==ASR: dest=Path("/weights/whisper")
        elif repo.endswith("Ace-Step1.5"): dest=CACHE
        else: dest=CACHE/repo.split("/")[1]
        dest.mkdir(exist_ok=True,parents=True)
        snapshot_download(repo,revision=item["revision"],local_dir=str(dest))
    return {k:v["revision"] for k,v in MANIFEST.items()}


class MusicEngineAdapter(Protocol):
    def render(self, job:dict, folder:Path, source:Path|None=None, task:str="text2music", instruction:str="")->Path: ...


class AceAdapter:
    def __init__(self, variant, lora=None):
        from acestep.handler import AceStepHandler
        self.variant=variant
        self.lm=None
        self.handler=AceStepHandler()
        msg,ok=self.handler.initialize_service(project_root=str(ROOT),config_path=VARIANTS[variant],device="cuda",
                                              use_flash_attention=False,offload_to_cpu=True)
        if not ok:raise RuntimeError(msg)
        if lora:
            result=self.handler.load_lora(str(lora))
            if not result.startswith("✅"):raise RuntimeError(result)
            state=self.handler.set_use_lora(True)
            if not state.startswith("✅"):raise RuntimeError(state)

    def render(self,job,folder,source=None,task="text2music",instruction=""):
        from acestep.inference import GenerationConfig,GenerationParams,generate_music
        folder.mkdir(exist_ok=True,parents=True)
        instrumental=job.get("mode") in ("instrumental","vocal_to_bgm")
        kw=dict(task_type=task,caption=", ".join(filter(None,[job.get("brief"),job.get("profile"),job.get("voice"),job.get("instruction")])),
                lyrics="[Instrumental]" if instrumental else job.get("lyrics",""),instrumental=instrumental,
                vocal_language=job.get("language","ru"),bpm=job.get("bpm",88),keyscale=job.get("key","Dm"),
                duration=job.get("duration_sec",40),seed=job.get("seed",2026),thinking=False,use_cot_metas=False,
                use_cot_caption=False,use_cot_language=False,src_audio=str(source) if source else None,
                inference_steps=8 if self.variant=="sketch" else 50,
                shift=3. if self.variant=="sketch" else 1.,guidance_scale=1. if self.variant=="sketch" else 7.,
                repainting_start=job.get("start_sec",0.),repainting_end=job.get("end_sec",-1.),
                chunk_mask_mode="explicit" if task=="repaint" else "auto")
        if task=="cover":
            kw["audio_cover_strength"]={"cover":.85,"style_transfer":.52,"vocal_to_bgm":.58}.get(job.get("mode"),.8)
        if self.variant=="studio" and task=="text2music":
            from acestep.llm_inference import LLMHandler
            if self.lm is None:
                self.lm=LLMHandler()
                status,ok=self.lm.initialize(checkpoint_dir=str(CACHE),lm_model_path="acestep-5Hz-lm-1.7B",
                                             backend="pt",device="cuda",offload_to_cpu=True,dtype=None)
                if not ok:raise RuntimeError(status)
            kw["thinking"]=True
        if instruction:kw["instruction"]=instruction
        config=GenerationConfig(batch_size=1,seeds=[kw["seed"]],use_random_seed=False,audio_format="wav")
        result=generate_music(self.handler,self.lm,GenerationParams(**kw),config,save_dir=str(folder))
        if not result.success or not result.audios:raise RuntimeError(result.error or result.status_message)
        path=Path(result.audios[0]["path"])
        if not path.is_file():raise IOError("ACE output missing")
        return path


# Trusted plugins may register a trained engine through the safar.music_engines entry point.
def adapter(variant,lora=None)->MusicEngineAdapter:
    configured=os.environ.get("SAFAR_ENGINE_"+variant.upper(),variant)
    if configured in VARIANTS:return AceAdapter(configured,lora)
    variant=configured
    from importlib.metadata import entry_points
    entries=entry_points(group="safar.music_engines",name=variant)
    if not entries:raise ValueError("Unknown engine")
    return next(iter(entries)).load()(variant,lora)


def clear_gpu():
    import torch
    gc.collect()
    torch.cuda.empty_cache()


def licensed_files(root, item):
    if not item.get("commercial_grant") or not item.get("sha256"):raise ValueError("Missing checkpoint grant or hashes")
    for name,expected in item["sha256"].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root.resolve()) or digest(path)!=expected:raise ValueError("Licensed file hash mismatch")


def roformer(src,sr,root,cfg):
    import torch
    import yaml
    from bs_roformer import BSRoformer,MelBandRoformer
    licensed_files(root,cfg)
    for key in ("config","weights"):
        if cfg[key] not in cfg["sha256"]:raise ValueError("Unverified RoFormer file")
    conf=yaml.safe_load((root/cfg["config"]).read_text())
    arch=BSRoformer if cfg["arch"]=="bs" else MelBandRoformer
    model=arch(**conf["model"])
    state=torch.load(root/cfg["weights"],map_location="cpu",weights_only=True)
    model.load_state_dict(state.get("state_dict",state),strict=True);model.eval().cuda()
    names=conf["training"]["instruments"]
    if set(names)!=set(STEMS):raise ValueError("Six named outputs required")
    rs=int(conf["audio"].get("sample_rate",44100));audio=align(src,sr,rs).astype("float32")
    n=len(audio);chunk=int(conf["audio"]["chunk_size"]);hop=chunk//2
    out=np.zeros((6,n,2),np.float32);weight=np.zeros(n,np.float32)
    window=np.maximum(.001,np.hanning(chunk)).astype(np.float32)
    with torch.inference_mode():
        for a in range(0,n,hop):
            end=min(n,a+chunk);size=end-a
            block=np.pad(audio[a:end],((0,chunk-size),(0,0)))
            pred=model(torch.from_numpy(block.T.copy()).cuda()[None]).detach().cpu().numpy()[0]
            out[:,a:end]+=pred[:,:,:size].transpose(0,2,1)*window[:size,None]
            weight[a:end]+=window[:size]
    return {k:align(out[i]/weight[:,None],rs,sr,2,len(src)) for i,k in enumerate(names)}


def demucs_six(src,sr,root,item):
    import subprocess
    import tempfile
    licensed_files(root,item)
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp);repo=path/"weights";repo.mkdir()
        for name in item["sha256"]:
            os.symlink((root/name).resolve(),repo/Path(name).name)
        audio=path/"source.wav";sf.write(audio,src,sr,subtype="FLOAT")
        subprocess.run(["/opt/demucs/bin/python","-m","engine.demucs_runner",str(audio),str(repo),str(path)],check=True)
        return {k:sf.read(path/(k+".wav"),always_2d=True,dtype="float32")[0] for k in STEMS}

def split(path,folder,job):
    src,sr=sf.read(path,always_2d=True,dtype="float32")
    src=align(src,sr,sr)
    root=Path("/weights/licensed")
    config=json.loads((root/"manifest.json").read_text()) if (root/"manifest.json").exists() else {}
    parts=None;route="ace-extract";warnings=[]
    for name,fn in (("roformer",roformer),("demucs",demucs_six)):
        if name not in config:continue
        try:
            parts=fn(src,sr,root,config[name]);route=name;break
        except Exception as exc:
            warnings.append(f"{name}: {type(exc).__name__}");clear_gpu()
    ace=adapter("extract")
    def extract(name):
        temp=ace.render(job,folder/"renders"/name,path,"extract",f"Extract the {name} track from the audio:")
        x,r=sf.read(temp,always_2d=True,dtype="float32")
        return align(x,r,sr,2,len(src))
    if parts is None:
        parts={k:extract("keyboard" if k=="piano" else k) for k in STEMS if k!="other"}
        parts["other"]=src-sum(parts.values())
    bg=extract("backing_vocals") if job.get("mode") not in ("instrumental","vocal_to_bgm") else np.zeros_like(src)
    # Use floating point working stems so residuals above full scale survive sync rendering.
    for k,v in {**parts,"Backing":bg}.items():sf.write(folder/(k+".wav"),v,sr,subtype="FLOAT")
    return {"separator":route,"warnings":warnings,"piano_family":"keyboard" if route=="ace-extract" else "piano"}
