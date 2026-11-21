"""Run licensed local Demucs bags in their own CUDA/PyTorch environment."""
import sys
from pathlib import Path
import soundfile as sf
import torch
from demucs.apply import apply_model
from demucs.pretrained import get_model
from .dsp import align

def run(source,repo,out):
    x,rate=sf.read(source,always_2d=True,dtype="float32")
    def separate(name):
        model=get_model(name,repo=repo).eval().cuda()
        audio=align(x,rate,model.samplerate)
        t=torch.from_numpy(audio.T.copy());ref=t.mean(0);mean=ref.mean();std=ref.std().clamp_min(1e-7)
        with torch.inference_mode():
            stems=apply_model(model,((t-mean)/std)[None],device="cuda",overlap=.25)[0]*std+mean
        return {k:align(v.cpu().numpy().T,model.samplerate,rate,2,len(x)) for k,v in zip(model.sources,stems)}
    four=separate("htdemucs_ft");six=separate("htdemucs_6s")
    for k in ("guitar","piano"):
        four[k]=six[k];four["other"]-=six[k]
    for k,data in four.items():sf.write(out/(k+".wav"),data,rate,subtype="FLOAT")

if __name__=="__main__":run(*map(Path,sys.argv[1:]))
