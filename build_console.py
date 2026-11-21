"""Build the dependency-free studio shell. All fonts, voice samples and DSP are embedded."""
from base64 import b64encode
from pathlib import Path

root=Path(__file__).resolve().parents[1]
src=root/"console"
def encoded(name):return b64encode((src/"assets"/name).read_bytes()).decode()
css=(src/"skin.css").read_text().replace("__INTER__",encoded("Inter.woff2")).replace("__MONO__",encoded("JetBrainsMono.woff2")).replace("__GRAIN__",encoded("grain.png"))
audio=(src/"audio.js").read_text().replace("/*__VOICE__*/{}",(src/"assets/voice.json").read_text())
doc=(src/"console.tpl").read_text()
for key,value in {"CSS":css,"ENGINE":audio,"UI":(src/"ui.js").read_text(),"CLOUD":(src/"cloud.js").read_text()}.items():
    doc=doc.replace("/*__"+key+"__*/",value)
notices="\n".join((root/"public"/n).read_text() for n in ("INTER-OFL.txt","JETBRAINS-OFL.txt"))
doc=doc.replace("</head>","<!-- Font notices\n"+notices.replace("--","—")+"\n-->\n</head>")
(root/"public/console.html").write_text(doc)
listen=(src/"listen.tpl").read_text().replace("/*__ENGINE__*/",(src/"audio.js").read_text().replace("/*__VOICE__*/{}","{}"))
(root/"public/listen.html").write_text(listen)
