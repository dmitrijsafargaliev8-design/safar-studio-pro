"""Bilingual producer notes from a pinned Qwen instruction model."""
from pathlib import Path

def review(job):
    import torch
    from transformers import AutoModelForCausalLM,AutoTokenizer
    path="/weights/review"
    tok=AutoTokenizer.from_pretrained(path,local_files_only=True)
    model=AutoModelForCausalLM.from_pretrained(path,local_files_only=True,torch_dtype=torch.float16).to("cuda").eval()
    messages=[{"role":"system","content":"Ты музыкальный A&R и вокальный режиссёр. На русском дай ровно три конкретные правки брифа и лирики: хук, ритм подачи, аранжировка. В каждом пункте привяжи совет к словам автора. Анализируй только текст, не утверждай, что слушал запись."},
              {"role":"user","content":f"{job.get('bpm')} BPM, {job.get('key')}. Бриф: {job.get('brief','')}\nТекст:\n{job.get('lyrics','')}"}]
    inp=tok(tok.apply_chat_template(messages,tokenize=False,add_generation_prompt=True),return_tensors="pt",truncation=True,max_length=5000).to(model.device)
    with torch.inference_mode():out=model.generate(**inp,max_new_tokens=300,do_sample=False,pad_token_id=tok.eos_token_id)
    text=tok.decode(out[0,inp.input_ids.shape[1]:],skip_special_tokens=True).strip()
    if not text:raise RuntimeError("A&R model returned no text")
    return {"review":text,"model":"Qwen2.5-1.5B-Instruct","model_license":"Apache-2.0","model_revision":"989aa7980e4cf806f80c7fef2b1adb7bc71aa306"}
