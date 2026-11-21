"""Sample-preserving edits, BS.1770 metering and delivery renders. Audio arrays are frames × channels."""
import hashlib
import json
import math
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy import ndimage, signal

TARGETS = {"streaming": -9.6, "club": -7.5, "apple": -14.0}
STEMS = ("vocals", "drums", "bass", "guitar", "piano", "other")


def digest(path):
    with open(path, "rb") as src:
        return hashlib.file_digest(src, "sha256").hexdigest()


def align(x, source_rate, rate, channels=2, frames=None):
    if x.ndim == 1:
        x = x[:, None]
    if source_rate != rate:
        d = math.gcd(source_rate, rate)
        x = signal.resample_poly(x, rate // d, source_rate // d, axis=0)
    if channels == 1:
        x = x.mean(axis=1, keepdims=True)
    elif x.shape[1] == 1:
        x = np.repeat(x, channels, axis=1)
    elif x.shape[1] != channels:
        raise ValueError("Expected mono or stereo audio")
    if frames is not None:
        x = x[:frames]
        if len(x) < frames:
            x = np.pad(x, ((0, frames - len(x)), (0, 0)))
    return x


def true_peak(x):
    # Overlap protects FIR boundaries without allocating a whole oversampled song.
    peak = 0.
    for start in range(0, len(x), 131072):
        end = min(len(x), start + 131072)
        a = max(0, start - 32)
        part = signal.resample_poly(x[a:min(len(x), end + 32)], 4, 1, axis=0)
        peak = max(peak, float(np.max(np.abs(part[(start-a)*4:(end-a)*4]))))
    return peak


def lufs(x, rate):
    if len(x) < math.ceil(.4 * rate):
        x = np.pad(x, ((0, math.ceil(.4 * rate)-len(x)), (0, 0)))
    measured = pyln.Meter(rate).integrated_loudness(x)
    return float(measured) if math.isfinite(measured) else None


def master(x, rate, target):
    if not len(x) or not np.isfinite(x).all():
        raise ValueError("Invalid audio samples")
    level = lufs(x, rate)
    if level is None:
        return x.copy()
    limit = 10**(-1.03/20)
    drive = 10**((target-level)/20)
    future = ndimage.maximum_filter1d(np.max(np.abs(x), axis=1), size=max(3, int(rate*.008)|1), mode="nearest")
    for _ in range(4):
        gain = np.minimum(drive, limit/np.maximum(future, 1e-9))
        # Smooth changes but retain the instantaneous peak constraint.
        smooth = ndimage.uniform_filter1d(gain, size=max(3, int(rate*.004)), mode="nearest")
        y = x * np.minimum(gain, smooth)[:, None]
        y *= min(1., limit/max(true_peak(y), 1e-12))
        actual = lufs(y, rate)
        if actual is None or abs(target-actual) < .18:
            break
        drive *= 10**(np.clip(target-actual, -4., 4.)/20)
    return y


def pcm24(path, x, rate):
    sf.write(str(path), x, rate, format="WAV", subtype="PCM_24")


def repaint(source, generated, result, start, end):
    info = sf.info(source)
    if info.format not in ("WAV", "WAVEX", "RF64") or info.subtype not in ("PCM_U8","PCM_16","PCM_24","PCM_32","FLOAT","DOUBLE"):
        raise ValueError("Bit-exact Repaint requires PCM or IEEE float WAV")
    n, rate = info.frames, info.samplerate
    if not (0 <= start < end <= n/rate + .5/rate):
        raise ValueError("Repaint region outside the source")
    a, b = int(round(start*rate)), min(n, int(round(end*rate)))
    if b <= a:
        raise ValueError("Region shorter than one sample")
    patch, rp = sf.read(generated, always_2d=True)
    patch = align(patch, rp, rate, info.channels)
    if len(patch) < b:
        raise ValueError("Model returned incomplete source-aligned audio")
    shutil.copyfile(source, result)
    with sf.SoundFile(result, "r+") as wav:
        wav.seek(a)
        orig = wav.read(b-a, always_2d=True)
        fade = min(int(.025*rate), (b-a)//2)
        w = np.ones((b-a,1))
        if fade:
            shape = .5-.5*np.cos(np.linspace(0, np.pi, fade))
            w[:fade,0], w[-fade:,0] = shape, shape[::-1]
        wav.seek(a)
        wav.write(orig*(1-w)+patch[a:b]*w)
    outside = 0.
    with sf.SoundFile(source) as before, sf.SoundFile(result) as after:
        for left, right in ((0,a),(b,n)):
            before.seek(left); after.seek(left)
            for pos in range(left,right,131072):
                size=min(131072,right-pos)
                outside=max(outside,float(np.max(np.abs(before.read(size,always_2d=True)-after.read(size,always_2d=True)))))
    if outside != 0.:
        raise ArithmeticError("Repaint failed exactness check on saved WAV")
    return {"outside_max_abs_diff":outside,"start_frame":a,"end_frame_exclusive":b,"sample_rate":rate,"subtype":info.subtype}


def rhythm_key(x, rate):
    # Use distributed excerpts to bound work on a ten-minute track.
    mono = x.mean(axis=1)
    excerpts = [mono] if len(mono)<=40*rate else [mono[i:i+12*rate] for i in np.linspace(0,len(mono)-12*rate,3,dtype=int)]
    chroma = np.zeros(12)
    tempos = []
    for excerpt in excerpts:
        y = align(excerpt, rate, 11025, 1)[:,0]
        if len(y)<2048 or np.max(abs(y))<1e-6:
            continue
        f, _, spec = signal.stft(y,fs=11025,nperseg=2048,noverlap=1792,boundary=None)
        mag = np.abs(spec)
        band=(f>55)&(f<3500)
        pitches=np.rint(69+12*np.log2(f[band]/440)).astype(int)%12
        energy=np.mean(mag[band]**1.5,axis=1)
        chroma+=np.bincount(pitches,weights=energy,minlength=12)
        onset=np.maximum(0,np.diff(np.log1p(2000*mag),axis=1)).sum(axis=0)
        if len(onset)>80 and np.std(onset)>1e-5:
            onset-=onset.mean()
            corr=signal.correlate(onset,onset,mode="full",method="fft")[len(onset)-1:]
            lo,hi=15,min(56,len(corr))
            j=lo+np.argmax(corr[lo:hi])
            if corr[j]>.08*corr[0]:
                tempos.append(60*11025/(256*j))
    if not chroma.any() or chroma.std()<1e-9:
        return None,None
    profiles=[[6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88],[6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17]]
    scores=[(np.corrcoef(chroma,np.roll(p,k))[0,1],k,m) for m,p in enumerate(profiles) for k in range(12)]
    score,k,m=max(scores)
    key="C C# D D# E F F# G G# A A# B".split()[k]+(" minor" if m else " major") if score>.25 else None
    return (round(float(np.median(tempos)),1) if tempos else None),key


def telemetry(path):
    x,sr=sf.read(path,always_2d=True,dtype="float32")
    tp=true_peak(x)
    report=subprocess.run(["ffmpeg","-hide_banner","-nostats","-i",str(path),"-af","ebur128=framelog=verbose","-f","null","-"],capture_output=True,text=True,check=True)
    m=re.findall(r"LRA:\s*([\d.]+) LU",report.stderr)
    bpm,key=rhythm_key(x,sr)
    corr=float(np.corrcoef(x.T)[0,1]) if x.shape[1]==2 and np.all(np.std(x,axis=0)>1e-8) else None
    return {"integrated_lufs":lufs(x,sr),"true_peak_dbtp":round(20*math.log10(tp),3) if tp else None,
            "lra_lu":float(m[-1]) if m and len(x)>=3*sr else None,"bpm_detected":bpm,"key_detected":key,
            "stereo_correlation":corr,"sample_rate":sr,"duration_sec":len(x)/sr,"wav_sha256":digest(path)}


def delivery(folder, cuts, preset):
    folder=Path(folder)
    master_path=folder/"Master.wav"
    original,sr=sf.read(master_path,always_2d=True,dtype="float32")
    def read_stem(name):
        x,r=sf.read(folder/(name+".wav"),always_2d=True,dtype="float32")
        return align(x,r,sr,original.shape[1],len(original))
    inst=sum(read_stem(s) for s in STEMS if s!="vocals")
    bg=read_stem("Backing") if (folder/"Backing.wav").exists() else np.zeros_like(inst)
    clean=original.copy()
    for lo,hi in cuts:
        if not (0<=lo<hi<=len(clean)/sr+.001):raise ValueError("Invalid Clean interval")
        a,b=max(0,round(lo*sr)),min(len(clean),round(hi*sr))
        fade=min(int(sr*.01),(b-a)//2)
        w=np.ones(b-a)
        if fade:
            v=.5-.5*np.cos(np.linspace(0,np.pi,fade));w[:fade]=v;w[-fade:]=v[::-1]
        clean[a:b]+=(inst[a:b]-clean[a:b])*w[:,None]
    for name,data in (("Instrumental",inst),("TV_Mix",inst+bg)):
        pcm24(folder/(name+".wav"),master(data,sr,TARGETS[preset]),sr)
    if cuts:
        pcm24(folder/"Clean.wav",master(clean,sr,TARGETS[preset]),sr)
    else:
        shutil.copyfile(master_path,folder/"Clean.wav")
    with zipfile.ZipFile(folder/"SyncPack.zip","w",allowZip64=True) as z:
        for name in ("Master.wav","Instrumental.wav","TV_Mix.wav","Clean.wav"):
            file=folder/name
            if sf.info(file).subtype=="PCM_24":
                z.write(file,name)
            else:
                import io
                audio,rate=sf.read(file,always_2d=True)
                binary=io.BytesIO()
                sf.write(binary,audio,rate,format="WAV",subtype="PCM_24")
                z.writestr(name,binary.getvalue())
    return {"clean_intervals":cuts,"tv_has_backing":bool(np.any(bg)),"clean_equals_master":digest(folder/"Clean.wav")==digest(master_path)}
