#!/usr/bin/env python3
"""Pruebas del motor compilado real; no dobles del DSP."""
import argparse, ctypes, json, math, random, subprocess, sys, tempfile, unittest, wave
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from christian_noise import Engine as NativeEngine,LATENCY,read_wav,write_wav
TEST_LIBRARY=None
TEST_STRENGTH=1.0
TEMP_DIR=ROOT/'build/test-tmp'
def Engine(strength=None):
    if strength is None: strength=TEST_STRENGTH
    return NativeEngine(strength,library=TEST_LIBRARY,latency=LATENCY)
RESULTS=[]
class Tests(unittest.TestCase):
    def setUp(self): self.e=Engine()
    def tearDown(self): self.e.close()
    def signal(self,n=15007):
        r=random.Random(42)
        return [0.15*math.sin(i*.031)+r.uniform(-.03,.03) for i in range(n)]
    def test_silence(self): self.assertTrue(all(x==0 for x in self.e.offline([0.0]*2009)))
    def test_empty(self): self.assertEqual(self.e.offline([]),[])
    def test_odd_length_and_finite(self):
        x=self.e.offline(self.signal()); self.assertEqual(len(x),15007)
        self.assertTrue(all(math.isfinite(v) and abs(v)<=1 for v in x))
    def test_partition_invariance(self):
        x=self.signal(); ref=self.e.offline(x,480)
        for block in (1,127,256,511,1024,4096): self.assertEqual(ref,self.e.offline(x,block))
    def test_reset_reproducible(self):
        x=self.signal(); self.assertEqual(self.e.offline(x),self.e.offline(x))
    def test_bypass_identity(self):
        with Engine(0) as e:
            x=self.signal(); self.assertLess(max(abs(a-b) for a,b in zip(x,e.offline(x,127))),1e-7)
    def test_bypass_delay(self):
        with Engine(0) as e:
            x=[.25]+[0.0]*2000; y=e.process(x)
            self.assertEqual([i for i,v in enumerate(y) if v], [LATENCY])
    def test_invalid_samples(self):
        for v in (math.nan,math.inf,-math.inf,1.1,-1.1):
            with self.assertRaises(ValueError): self.e.process([0,v])
    def test_invalid_does_not_advance(self):
        with self.assertRaises(ValueError): self.e.process([.1]*99+[math.nan])
        x=self.signal()
        with Engine() as other: self.assertEqual(self.e.process(x),other.process(x))
    def test_null_pointer_validation(self):
        self.assertEqual(self.e.lib.cn_process(self.e.handle,None,None,1),-1)
        self.assertEqual(self.e.lib.cn_process(self.e.handle,None,None,0),0)
    def test_invalid_config(self):
        for rate,ch,s in ((44100,1,1),(48000,2,1),(48000,0,1),(48000,1,-.1),(48000,1,1.1),(48000,1,math.nan)):
            self.assertIsNone(self.e.lib.cn_create(rate,ch,s))
    def test_strength_validation(self):
        for s in (-1,2,math.nan): self.assertEqual(self.e.lib.cn_set_strength(self.e.handle,s),-1)
        self.assertEqual(self.e.lib.cn_set_strength(self.e.handle,.5),0)
    def test_instances_independent(self):
        self.e.process(self.signal())
        with Engine() as a,Engine() as b: self.assertEqual(a.process(self.signal()),b.process(self.signal()))
    def test_in_place(self):
        x=self.signal(); n=len(x); buf=(ctypes.c_float*n)(*x)
        self.assertEqual(self.e.lib.cn_process(self.e.handle,buf,buf,n),0)
        with Engine() as other: self.assertEqual(list(buf),other.process(x))
    def test_full_scale(self):
        y=self.e.offline([1.0,-1.0]*2400)
        self.assertTrue(all(math.isfinite(x) and abs(x)<=1 for x in y))
    def test_closed_engine(self):
        self.e.close()
        with self.assertRaises(ValueError): self.e.process([0])
    def test_wav_roundtrip(self):
        # Temporary files stay inside the authorized data directory.
        with tempfile.TemporaryDirectory(dir=TEMP_DIR) as d:
            p=Path(d)/'test.wav'; x=self.signal(1237); write_wav(p,x); y=read_wav(p)
            self.assertEqual(len(x),len(y)); self.assertLess(max(abs(a-b) for a,b in zip(x,y)),1/32768)
    def test_wrong_wav_rejected(self):
        with tempfile.TemporaryDirectory(dir=TEMP_DIR) as d:
            p=Path(d)/'bad.wav'
            with wave.open(str(p),'wb') as f:
                f.setnchannels(2); f.setsampwidth(2); f.setframerate(44100); f.writeframes(bytes(400))
            with self.assertRaises(ValueError): read_wav(p)
    def test_truncated_wav_rejected(self):
        with tempfile.TemporaryDirectory(dir=TEMP_DIR) as d:
            p=Path(d)/'bad.wav'; write_wav(p,[0]*100); p.write_bytes(p.read_bytes()[:-10])
            with self.assertRaises(ValueError): read_wav(p)
    def test_white_noise_quality_gate_and_bypass_countercheck(self):
        # Preserve the 6 dB gate even if this baseline fails it. Check the
        # negative control FIRST so a wet-quality failure cannot skip it.
        r=random.Random(73); x=[r.uniform(-.1,.1) for _ in range(48000)]
        wet=self.e.offline(x); start=14400
        attenuation=lambda y: 10*math.log10(sum(v*v for v in x[start:])/max(1e-20,sum(v*v for v in y[start:])))
        with Engine(0) as e: bypass_db=attenuation(e.offline(x))
        wet_db=attenuation(wet)
        print(json.dumps({'probe':'white_noise_gate','wet_attenuation_db':wet_db,'bypass_attenuation_db':bypass_db,'required_wet_db':6}),flush=True)
        self.assertLess(abs(bypass_db),.001)
        self.assertGreater(wet_db,6)
    def test_lowpass_noise_quality_gate(self):
        r=random.Random(74); previous=0; x=[]
        for _ in range(96000):
            previous=.96*previous+.04*r.uniform(-.2,.2); x.append(previous)
        wet=self.e.offline(x); start=24000
        reduction=10*math.log10(sum(v*v for v in x[start:])/max(1e-20,sum(v*v for v in wet[start:])))
        self.assertGreater(reduction,6)
class Record(unittest.TextTestResult):
    def addSuccess(self,test): super().addSuccess(test); RESULTS.append({'test':test.id(),'status':'passed'})
    def addFailure(self,test,err): super().addFailure(test,err); RESULTS.append({'test':test.id(),'status':'failed','error':str(err[1])})
    def addError(self,test,err): super().addError(test,err); RESULTS.append({'test':test.id(),'status':'error','error':str(err[1])})
if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--library',type=Path)
    ap.add_argument('--strength',type=float,default=1.0)
    ap.add_argument('--temp-dir',type=Path,default=TEMP_DIR)
    ap.add_argument('--latency',type=int,default=960,choices=(960,1440))
    ap.add_argument('--result',type=Path,default=ROOT/'results/unit-tests.json')
    args=ap.parse_args(); TEST_LIBRARY=args.library; LATENCY=args.latency; TEST_STRENGTH=args.strength; TEMP_DIR=args.temp_dir
    TEMP_DIR.mkdir(parents=True,exist_ok=True)
    result=unittest.TextTestRunner(verbosity=2,resultclass=Record).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
    (ROOT/'results').mkdir(exist_ok=True)
    args.result.parent.mkdir(parents=True,exist_ok=True)
    args.result.write_text(json.dumps({'count':result.testsRun,'ok':result.wasSuccessful(),'library':str(TEST_LIBRARY),'latency_samples':LATENCY,'strength':TEST_STRENGTH,'results':RESULTS},indent=2)+'\n')
    sys.exit(not result.wasSuccessful())
