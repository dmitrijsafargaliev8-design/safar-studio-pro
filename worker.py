import base64
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import numpy as np
import soundfile as sf

from . import dsp,models

DATA=Path("/data")
BAD_WORDS=re.compile(r"^(fuck\w*|shit\w*|бля[дт]\w*|ёб\w*|еба\w*|хуй\w*|пизд\w*)$",re.I)


def stage(name):
    print("SAFAR_EVENT "+json.dumps({"stage":name}),flush=True)


def words(path,lang,lyrics):
    from faster_whisper import WhisperModel
    model=WhisperModel("/weights/whisper",device="cuda",compute_type="int8_float16")
    segs,info=model.transcribe(str(path),language=lang or None,word_timestamps=True,beam_size=3,vad_filter=False)
    timed=[{"word":w.word.strip(),"start":w.start,"end":w.end,"probability":w.probability} for s in segs for w in (s.words or [])]
    norm=lambda s:re.findall(r"[^\W_]+",s.lower(),re.UNICODE)
    ref=norm(re.sub(r"\[[^\]]*\]"," ",lyrics))
    got=norm(" ".join(w["word"] for w in timed))
    edits=[{"op":op,"expected":" ".join(ref[i:j]),"heard":" ".join(got[a:b])} for op,i,j,a,b in difflib.SequenceMatcher(None,ref,got,autojunk=False).get_opcodes() if op!="equal"] if ref else []
    return timed,{"language":info.language,"differences":edits,"low_confidence":[w for w in timed if w["probability"]<.55]}


def path_for(owner,job):return DATA/"jobs"/owner/job


def train(job,folder):
    stage("LoRA Training")
    with tempfile.TemporaryDirectory() as tmp:
        raw=Path(tmp)/"audio";raw.mkdir()
        for i,uid in enumerate(job["upload_ids"]):
            source=DATA/"uploads"/job["owner"]/uid
            if not source.is_file():raise FileNotFoundError("Training upload unavailable")
            subprocess.run(["ffmpeg","-v","error","-y","-i",str(source),"-map","0:a:0","-t","600","-ar","48000","-ac","2",str(raw/f"track_{i}.wav")],check=True)
        tensors=Path(tmp)/"tensors";out=Path(tmp)/"output"
        cmd=[sys.executable,str(models.ROOT/"train.py"),"fixed","--checkpoint-dir",str(models.CACHE),"--model-variant",models.VARIANTS[job["model"]]]
        subprocess.run(cmd+["--preprocess","--audio-dir",str(raw),"--tensor-output",str(tensors)],cwd=models.ROOT,check=True)
        subprocess.run(cmd+["--dataset-dir",str(tensors),"--output-dir",str(out),"--epochs",str(job.get("epochs",50))],cwd=models.ROOT,check=True)
        final=out/"final"
        if not (final/"adapter_model.safetensors").is_file() or not (final/"adapter_config.json").is_file():raise RuntimeError("Trainer did not produce PEFT adapter")
        shutil.copytree(final,folder/"adapter",dirs_exist_ok=True)
    return {"lora_id":job["id"],"model":job["model"],"name":job["name"],"sha256":dsp.digest(folder/"adapter"/"adapter_model.safetensors")}


def run(job,folder):
    if job["kind"]=="review":
        from .review import review
        stage("A&R Review")
        return review(job)
    if job["kind"]=="train":return train(job,folder)
    stage("Generating")
    source=None
    if job.get("parent_id"):source=path_for(job["owner"],job["parent_id"])/"Master.wav"
    elif job.get("upload_id"):source=DATA/"uploads"/job["owner"]/job["upload_id"]
    elif job.get("base_wav_b64"):
        source=folder/"source.wav";source.write_bytes(base64.b64decode(job["base_wav_b64"],validate=True))
    if source and not source.is_file():raise ValueError("Source is missing")
    ref=job.get("lora_id");lora=None
    if ref:
        p=path_for(job["owner"],ref)
        if json.loads((p/"result.json").read_text())["model"]!=job["model"]:raise ValueError("LoRA model mismatch")
        lora=p/"adapter"
    a=models.adapter(job["model"],lora)
    provenance=getattr(a,"provenance",None)
    if provenance is None and isinstance(a,models.AceAdapter):
        repo="ACE-Step/acestep-v15-xl-sft" if job["model"]=="studio" else "ACE-Step/Ace-Step1.5"
        provenance={"model":models.VARIANTS[job["model"]],"model_license":"MIT","model_revision":models.MANIFEST[repo]["revision"]}
    if not provenance or not all(provenance.get(k) for k in ("model","model_license","model_revision")):
        raise ValueError("Engine must publish pinned model provenance")
    task="repaint" if job["kind"]=="repaint" else "cover" if job.get("mode") in ("cover","style_transfer","vocal_to_bgm") else "text2music"
    with tempfile.TemporaryDirectory() as tmp:
        wav=a.render(job,Path(tmp),source,task)
        del a;models.clear_gpu()
        stage("Mastering")
        target=folder/"Master.wav"
        if task=="repaint":info=dsp.repaint(source,wav,target,job["start_sec"],job["end_sec"])
        else:
            x,sr=sf.read(wav,always_2d=True,dtype="float32")
            if job.get("mode")=="vocal_to_bgm":
                extractor=models.adapter("extract")
                voice=extractor.render(job,Path(tmp)/"guide-vocal",wav,"extract","Extract the vocals track from the audio:")
                v,vr=sf.read(voice,always_2d=True,dtype="float32")
                x=x-dsp.align(v,vr,sr,x.shape[1],len(x))
                del extractor;models.clear_gpu()
            dsp.pcm24(target,dsp.master(dsp.align(x,sr,sr),sr,dsp.TARGETS[job["preset"]]),sr);info={}
    telemetry=dsp.telemetry(target)
    info.update({**provenance,"telemetry":telemetry,"seed":job["seed"]})
    subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y","-i",str(target),"-c:a","libmp3lame","-b:a","192k",str(folder/"Master.mp3")],check=True)
    if job.get("split_stems",True):
        stage("6-Stem Split");info.update(models.split(target,folder,job));shutil.rmtree(folder/"renders",ignore_errors=True);models.clear_gpu()
    stage("Diction QA")
    if job.get("mode") in ("instrumental","vocal_to_bgm"):
        timed,qa=[],{"instrumental":True}
    else:timed,qa=words(folder/"vocals.wav" if (folder/"vocals.wav").exists() else target,job.get("language"),job.get("lyrics",""))
    (folder/"words.json").write_text(json.dumps({"words":timed,"qa":qa},ensure_ascii=False))
    if job.get("split_stems",True):
        cuts=job.get("clean_intervals") or [[max(0,w["start"]-.025),min(telemetry["duration_sec"],w["end"]+.035)] for w in timed if BAD_WORDS.match(w["word"].strip(".,!:;?"))]
        info["delivery"]=dsp.delivery(folder,cuts,job["preset"])
        with zipfile.ZipFile(folder/"Stems.zip","w") as z:
            for name in dsp.STEMS:
                path=folder/(name+".wav");x,sr=sf.read(path,always_2d=True,dtype="float32")
                dsp.pcm24(path,x,sr);z.write(path,path.name)
                subprocess.run(["ffmpeg","-v","error","-y","-i",str(path),"-t","12","-b:a","192k",str(folder/(name+"-preview.mp3"))],check=True)
    info["asr_transcript"]=" ".join(w["word"] for w in timed)
    return info


if __name__=="__main__":
    job=json.loads(Path(sys.argv[1]).read_text());folder=path_for(job["owner"],job["id"]);folder.mkdir(parents=True,exist_ok=True)
    info=run(job,folder);(folder/"result.json").write_text(json.dumps(info,ensure_ascii=False,allow_nan=False))
