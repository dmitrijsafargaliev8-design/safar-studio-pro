"""Local Chromium acceptance test. Set CHROME_BIN if the browser executable is external."""
import io
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path
import numpy as np
import soundfile as sf
from playwright.sync_api import sync_playwright

def run():
    html=Path(os.environ.get("SAFAR_HTML",Path(__file__).resolve().parents[1]/"public/console.html")).resolve()
    with tempfile.TemporaryDirectory() as tmp,sync_playwright() as pw:
        root=Path(tmp)
        sr=32000;t=np.arange(2*sr)/sr
        sf.write(root/"input.wav",np.stack([.2*np.sin(461*2*np.pi*t),.15*np.sin(229*2*np.pi*t)],1),sr,subtype="PCM_24")
        subprocess.run(["ffmpeg","-loglevel","error","-i",str(root/"input.wav"),"-b:a","192k",str(root/"input.mp3")],check=True)
        browser=pw.chromium.launch(executable_path=os.environ.get("CHROME_BIN"),args=["--no-sandbox","--disable-gpu","--no-zygote","--single-process","--autoplay-policy=no-user-gesture-required"])
        page=browser.new_page(viewport={"width":1460,"height":960},accept_downloads=True)
        errors=[];internet=[];page.on("pageerror",lambda e:errors.append(str(e)))
        page.on("request",lambda r:internet.append(r.url) if r.url.startswith(("https:","http:")) else None)
        page.goto(html.as_uri());page.wait_for_function("peaks.length>0")
        assert page.locator(".strip").count()==6
        page.locator("#playTop").click();page.wait_for_timeout(650)
        assert page.locator("#time").inner_text()!="00:00.0" and page.locator("#lufs").inner_text()!="−∞"
        page.get_by_label("Drums solo").click();page.get_by_label("Vocals mute").click()
        assert page.get_by_label("Drums solo").get_attribute("aria-pressed")=="true"
        page.get_by_label("Piano pan").fill("0.64");page.get_by_label("Bass volume").fill("-14")
        page.locator("#resetMix").click()
        page.locator("#abA").click();page.keyboard.press("KeyB");page.wait_for_function("blind && blind.pick===1")
        diff=page.evaluate("Math.abs(integrated(blind.data[0],rate)-integrated(blind.data[1],rate))")
        assert diff<.1,diff
        page.locator("#abReveal").click();assert "мастер" in page.locator("#abInfo").inner_text()
        page.locator("#rew").click();page.evaluate("halt();blind=null;mix=newMix();")
        box=page.locator("#wave").bounding_box();x,y=box['x'],box['y']+40;w=box['width']
        page.mouse.move(x+.25*w+2,y);page.mouse.down();page.mouse.move(x+.50*w-2,y,steps=7);page.mouse.up()
        assert page.locator("#barA").input_value()=="3" and page.locator("#barB").input_value()=="4"
        page.evaluate("window.baseline=renderMix()");page.locator("#repaint").click()
        diffs=page.evaluate("""()=>{const out=renderMix();let outer=0,inner=0;const a=Math.round(2*barSec()*rate),b=Math.round(4*barSec()*rate);
            for(let c=0;c<2;c++)for(let i=0;i<len();i++){const diff=Math.abs(out[c][i]-window.baseline[c][i]);if(i<a||i>=b)outer=Math.max(outer,diff);else inner=Math.max(inner,diff)}return {outer,inner}}""")
        assert diffs["outer"]==0 and diffs["inner"]>.01
        page.locator("#wallet").click();page.locator(".plan button").nth(1).click();assert page.locator("#balance").inner_text()=="600"
        page.locator("#ups button").first.click();assert page.locator("#balance").inner_text()=="1100";page.locator("#close").click()
        page.locator("#exportPack").click();page.locator("#cleanCuts").fill(".8–1.1".replace(".8","0.8"))
        with page.expect_download() as result:page.get_by_text("Скачать 24-bit ZIP").click()
        result.value.save_as(root/"pack.zip")
        with zipfile.ZipFile(root/"pack.zip") as z:
            assert set(z.namelist())=={"Master.wav","Clean.wav","TV_Mix.wav","Instrumental.wav"} and z.testzip() is None
            for name in z.namelist():assert sf.info(io.BytesIO(z.read(name))).subtype=="PCM_24"
        for name in ("input.wav","input.mp3"):
            page.locator("#import").click();page.locator("#dest").select_option("mix")
            with page.expect_file_chooser() as fc:page.get_by_text("Выбрать файл",exact=True).click()
            fc.value.set_files(str(root/name));page.wait_for_function("sourceKind==='mix' && dur()<3 && !document.getElementById('modal').open")
            assert page.locator("#songName").inner_text().startswith("input")
        page.locator("#reel").click()
        with page.expect_download(timeout=15000) as rec:page.wait_for_timeout(3500)
        path=root/rec.value.suggested_filename;rec.value.save_as(path)
        meta=json.loads(subprocess.check_output(["ffprobe","-v","error","-show_streams","-of","json",str(path)]))
        vid=next(s for s in meta["streams"] if s["codec_type"]=="video")
        assert vid["width"]*16==vid["height"]*9 and any(s["codec_type"]=="audio" for s in meta["streams"])
        page.set_viewport_size({"width":393,"height":850});page.wait_for_timeout(150);assert page.evaluate("innerWidth===document.documentElement.scrollWidth")
        assert not errors,errors;assert not internet,internet
        print(json.dumps({"browser":browser.version,"offline_network_requests":0,"ab_delta_lu":diff,"repaint":diffs,"wav_pack":4,"imports":["WAV","MP3"],"reel_codec":vid["codec_name"],"reel_size":[vid["width"],vid["height"]],"mobile_width":393,"javascript_errors":errors},ensure_ascii=False));browser.close()
if __name__=="__main__":run()
