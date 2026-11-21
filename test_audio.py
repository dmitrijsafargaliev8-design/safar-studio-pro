import tempfile
import unittest
import zipfile
from pathlib import Path
import numpy as np
import soundfile as sf
from engine import dsp


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.sr=16000
        t=np.arange(4*self.sr)/self.sr
        self.x=np.stack([.24*np.sin(2*np.pi*220*t),.19*np.sin(2*np.pi*330*t)],1)
    def test_exact_after_encoding(self):
        for subtype in ('PCM_16','PCM_24','PCM_32','FLOAT','DOUBLE'):
            for channels in (1,2):
                with self.subTest(subtype=subtype,channels=channels):
                    src=self.root/'source.wav';gen=self.root/'new.wav';out=self.root/'out.wav'
                    sf.write(src,self.x[:,:channels],self.sr,subtype=subtype)
                    sf.write(gen,self.x[::2]*-.8,self.sr//2,subtype='FLOAT')
                    for a,b in ((0,.5),(.381,1.726),(3.1,4)):
                        ev=dsp.repaint(src,gen,out,a,b)
                        x,_=sf.read(src,always_2d=True);y,_=sf.read(out,always_2d=True)
                        i,j=ev['start_frame'],ev['end_frame_exclusive']
                        self.assertTrue(np.array_equal(x[:i],y[:i]) and np.array_equal(x[j:],y[j:]))
                        self.assertGreater(np.max(abs(y[i:j]-x[i:j])),.02);self.assertEqual(ev['outside_max_abs_diff'],0)
                    with self.assertRaises(ValueError):dsp.repaint(src,gen,out,3,2)
    def test_targets_and_artifacts(self):
        for preset,target in dsp.TARGETS.items():
            x=dsp.master(self.x,self.sr,target);self.assertLessEqual(dsp.true_peak(x),10**(-1/20));self.assertAlmostEqual(dsp.lufs(x,self.sr),target,delta=.3)
        master=self.root/'Master.wav';dsp.pcm24(master,self.x,self.sr);meta=dsp.telemetry(master)
        self.assertEqual(len(meta['wav_sha256']),64);self.assertIsNotNone(meta['true_peak_dbtp'])
        for stem in dsp.STEMS:sf.write(self.root/(stem+'.wav'),self.x/6,self.sr,subtype='FLOAT')
        sf.write(self.root/'Backing.wav',self.x/20,self.sr,subtype='FLOAT')
        ev=dsp.delivery(self.root,[(.8,1.4)],'streaming');self.assertTrue(ev['tv_has_backing']);self.assertFalse(ev['clean_equals_master'])
        with zipfile.ZipFile(self.root/'SyncPack.zip') as z:
            self.assertEqual(set(z.namelist()),{'Master.wav','Instrumental.wav','TV_Mix.wav','Clean.wav'});self.assertIsNone(z.testzip())
            for f in z.namelist():self.assertEqual(sf.info(self.root/f).subtype,'PCM_24')
        self.assertNotEqual(dsp.digest(self.root/'TV_Mix.wav'),dsp.digest(self.root/'Instrumental.wav'))
    def test_inter_sample_peak_after_write(self):
        sr=48000
        t=np.arange(sr*4)/sr
        x=.018*np.sin(2*np.pi*180*t)
        x[sr:sr+4]=[.96,.96,-.96,-.96]
        self.assertGreater(dsp.true_peak(x[:,None]),.96)
        result=dsp.master(np.stack([x,-x],1),sr,-7.5)
        file=self.root/"ceiling.wav";dsp.pcm24(file,result,sr)
        saved,_=sf.read(file,always_2d=True)
        self.assertLessEqual(20*np.log10(dsp.true_peak(saved)),-1.0)
    def test_silence(self):
        x=np.zeros((8000,2));self.assertIsNone(dsp.lufs(x,self.sr));self.assertIsNone(dsp.rhythm_key(x,self.sr)[1]);self.assertTrue(np.isfinite(dsp.master(x,self.sr,-14)).all())


if __name__=='__main__':unittest.main()
