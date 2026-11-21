"""Build a standalone Colab notebook from the exact GPU sources in this tree."""
import base64
import hashlib
import io
import json
from pathlib import Path
import textwrap
import zipfile

root=Path(__file__).resolve().parents[1]
paths=[root/"safar_modal_server.py",*sorted((root/"engine").glob("*.py")),root/"engine/models.json"]
buf=io.BytesIO()
with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    for p in paths:
        zi=zipfile.ZipInfo(p.relative_to(root).as_posix(),date_time=(2026,1,1,0,0,0));zi.compress_type=zipfile.ZIP_DEFLATED
        z.writestr(zi,p.read_bytes())
blob=buf.getvalue();encoded=base64.b64encode(blob).decode()
nb={"nbformat":4,"nbformat_minor":5,"metadata":{"kernelspec":{"display_name":"Python 3","language":"python","name":"python3"},"language_info":{"name":"python"},"colab":{"provenance":[]}},"cells":[]}
def cell(kind,name,s):
    c={"cell_type":kind,"id":name,"metadata":{},"source":textwrap.dedent(s).strip().splitlines(keepends=True)}
    if kind=="code":c.update(execution_count=None,outputs=[])
    nb["cells"].append(c)
cell("markdown","intro",'''# SAFAR STUDIO PRO · Modal GPU Engine v5.0
Открой этот файл в Google Colab и выбери **Среда выполнения → Выполнить все**. Понадобятся Modal Token ID и Token Secret аккаунта с доступом к GPU. Их можно сохранить в секретах Colab под именами `MODAL_TOKEN_ID` и `MODAL_TOKEN_SECRET` или ввести в два скрытых поля при запуске. В Colab достаточно CPU.

Запуск создаёт приложение `safar-studio-pro`, два тома, словарь задач и закрытый секрет в выбранном Modal workspace. Сборка образов и первая загрузка весов занимают время. Завершающая проверка запускает платный короткий GPU-рендер. Повторный запуск обновляет эту версию приложения, сохраняет веса, результаты и действующий токен студии. Исходное приложение v4.1 имеет отдельное имя.''')
cell('code','install','''
import os, sys, subprocess, importlib, getpass, secrets
from pathlib import Path
subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "modal==1.6.4"], check=True)
import modal
def credential(name):
    if os.environ.get(name):
        return os.environ[name].strip()
    try:
        from google.colab import userdata
        value=userdata.get(name)
    except Exception:
        value=None
    return (value or getpass.getpass(name+"  ")).strip()
for name in ("MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"):
    os.environ[name]=credential(name)
    if not os.environ[name]: raise ValueError("Пустой ключ "+name)
modal.Secret.objects.create("safar-studio-pro-auth", {"SAFAR_GPU_TOKEN": secrets.token_urlsafe(48)}, allow_existing=True)
print("Доступ к Modal подтверждён.")
''')
cell('code','extract',"""import base64, hashlib, io, json, zipfile, py_compile, tempfile
from pathlib import Path
payload=base64.b64decode(\n"""+'\n'.join('    "'+encoded[i:i+96]+'"' for i in range(0,len(encoded),96))+'''\n)
assert hashlib.sha256(payload).hexdigest()=="'''+hashlib.sha256(blob).hexdigest()+'''"
source_root=Path(tempfile.mkdtemp(prefix="safar-pro-v5-"))
with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
    for info in bundle.infolist():
        target=(source_root/info.filename).resolve()
        if not target.is_relative_to(source_root): raise ValueError("Некорректный путь")
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(bundle.read(info))
for file in source_root.rglob("*.py"): py_compile.compile(str(file),doraise=True)
print("Исходники и SHA-256 проверены.")
''')
cell('code','deploy','''
import subprocess, sys
subprocess.run([sys.executable,"-m","modal","deploy","safar_modal_server.py"],cwd=source_root,check=True)
prepare=modal.Function.from_name("safar-studio-pro","prepare_models")
print("Подготовка закреплённых ревизий весов в постоянном томе…",flush=True)
print(prepare.remote())
URL=modal.Function.from_name("safar-studio-pro","api").get_web_url()
if not URL or not URL.startswith("https://") or ".modal.run" not in URL:raise RuntimeError("Modal не вернул HTTPS-адрес")
TOKEN=modal.Function.from_name("safar-studio-pro","deployment_token").remote()
print("Адрес API",URL)
''')
cell('code','verify','''
import json, time, uuid, urllib.request, urllib.error
def api_request(path,body=None):
    req=urllib.request.Request(URL.rstrip("/")+path,data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type":"application/json","Authorization":"Bearer "+TOKEN})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req,timeout=75) as response:return json.load(response)
        except (TimeoutError,urllib.error.URLError) as exc:
            if isinstance(exc,urllib.error.HTTPError) and exc.code<500:raise
            if attempt==3:raise
            time.sleep(2**attempt)
health=api_request("/health");assert health["version"]=="5.0.0"
test=api_request("/api/jobs",{"id":str(uuid.uuid4()),"kind":"generate","model":"sketch","brief":"Muted piano and a restrained breakbeat, instrumental, D minor","mode":"instrumental","bpm":88,"duration_sec":12,"split_stems":False,"seed":4187})
deadline=time.monotonic()+3600;previous=None
while time.monotonic()<deadline:
    status=api_request("/api/jobs/"+test["id"])
    if status["stage"]!=previous:print(status["stage"],flush=True);previous=status["stage"]
    if status["state"]=="failed":raise RuntimeError(status.get("error","GPU render failed"))
    if status["state"]=="done":break
    time.sleep(5)
else:raise TimeoutError("Срок ожидания прогрева истёк: "+test["id"])
evidence=status["result"]["telemetry"]
if evidence["integrated_lufs"] is None or evidence["true_peak_dbtp"] is None or evidence["true_peak_dbtp"]>-.99 or len(evidence["wav_sha256"])!=64:
    raise RuntimeError("Проверка мастер-файла не пройдена")
print("GPU-рендер подтверждён.",URL)
print(json.dumps(evidence,ensure_ascii=False,indent=2))
''')
cell('code','handoff','''
# Кнопка выдаёт приватную пару значений для переменных окружения Vercel.
import os, tempfile
from pathlib import Path
import ipywidgets as widgets
from IPython.display import display
button=widgets.Button(description="Скачать настройки Vercel",layout=widgets.Layout(width="260px"))
def export_settings(_):
    from google.colab import files
    with tempfile.NamedTemporaryFile(mode="w",prefix="SAFAR_MODAL_",suffix=".env",delete=False) as private:
        private.write("MODAL_URL="+URL+"\\nSAFAR_GPU_TOKEN="+TOKEN+"\\n")
    os.chmod(private.name,0o600)
    files.download(private.name)
button.on_click(export_settings)
display(button)
''')
cell("markdown","end",'''Перенеси значения скачанного файла в серверные переменные окружения Vercel. URL обслуживает защищённое API. Интерфейс студии находится в отдельном Next.js-проекте. Работу первого короткого Sketch подтверждает вывод предыдущей ячейки. XL, LoRA, разделение и тестовый платёж проверяются отдельно по инструкции `LAUNCH_IN_5_MIN.md`.''')
(root/"DEPLOY_SAFAR_PRO_TO_MODAL.ipynb").write_text(json.dumps(nb,ensure_ascii=False,indent=1)+"\n")
