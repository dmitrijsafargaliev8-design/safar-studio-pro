from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field, model_validator


class Job(BaseModel):
    id: UUID | None=None
    owner: UUID=UUID(int=0)
    kind: Literal["generate","repaint","train","review"]="generate"
    brief: str=Field("",max_length=3000)
    lyrics: str=Field("",max_length=16000)
    profile: str=Field("night_city_drive",max_length=200)
    voice: str=Field("",max_length=150)
    mode: Literal["song_with_vocals","instrumental","cover","style_transfer","vocal_to_bgm"]="song_with_vocals"
    model: Literal["sketch","studio"]="sketch"
    preset: Literal["streaming","club","apple"]="streaming"
    duration_sec: float=Field(40,ge=12,le=600,allow_inf_nan=False)
    bpm: int=Field(124,ge=30,le=300)
    key: str=Field("Am",max_length=24)
    seed: int=Field(2026,ge=0,le=2147483647)
    language: str=Field("ru",pattern=r"^[a-z]{2,3}$")
    split_stems: bool=True
    instruction: str=Field("",max_length=2000)
    start_sec: float=Field(0,ge=0,allow_inf_nan=False)
    end_sec: float=Field(0,ge=0,allow_inf_nan=False)
    base_wav_b64: str | None=Field(None,max_length=180_000_000)
    parent_id: UUID | None=None
    upload_id: UUID | None=None
    lora_id: UUID | None=None
    upload_ids: list[UUID]=Field(default_factory=list,max_length=10)
    epochs: int=Field(50,ge=1,le=200)
    name: str=Field("Artist",max_length=80)
    rights_confirmed: bool=False
    priority: bool=False
    completion_url: str | None=Field(None,pattern=r"^https://[a-zA-Z0-9.-]+(?::[0-9]+)?/api/jobs/notify$",max_length=350)
    completion_mac: str | None=Field(None,pattern=r"^[a-f0-9]{64}$")
    clean_intervals: list[tuple[float,float]]=Field(default_factory=list,max_length=256)

    def check_task(self,kind):
        if kind=="train" and (not self.rights_confirmed or not 5<=len(set(self.upload_ids))<=10):raise ValueError("5–10 unique, rights-cleared recordings required")
        if kind=="repaint" and not (0<=self.start_sec<self.end_sec<=600):raise ValueError("Invalid repaint interval")
        if (kind=="repaint" or self.mode in ("cover","style_transfer","vocal_to_bgm")) and sum(bool(v) for v in (self.upload_id,self.parent_id,self.base_wav_b64))!=1:raise ValueError("Exactly one source required")
        if any(not 0<=a<b<=600 for a,b in self.clean_intervals):raise ValueError("Invalid Clean interval")


class Ticket(BaseModel):
    owner: UUID
    job_id: UUID | None=None
    file: str=Field("",max_length=70)
    op: Literal["read","upload"]="read"
