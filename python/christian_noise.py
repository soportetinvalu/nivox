#!/usr/bin/env python3
"""CLI WAV PCM16/Float32 mono/48 kHz; salida PCM16, sin conversión de tasa/canales."""
import argparse, array, ctypes, json, math, platform, sys, time, wave
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
RATE=48000
LATENCY=960
class Engine:
    def __init__(self,strength=1.0,library=None,latency=LATENCY):
        if latency != 960: raise ValueError('SpeexDSP requires 960 samples latency')
        self.latency=latency
        library=library or ROOT/'build'/('libchristian_noise.dylib' if platform.system()=='Darwin' else 'libchristian_noise.so')
        self.lib=ctypes.CDLL(str(library))
        self.lib.cn_create.argtypes=[ctypes.c_uint,ctypes.c_uint,ctypes.c_float]
        self.lib.cn_create.restype=ctypes.c_void_p
        self.lib.cn_process.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_float),ctypes.POINTER(ctypes.c_float),ctypes.c_size_t]
        self.lib.cn_process.restype=ctypes.c_int
        self.lib.cn_destroy.argtypes=[ctypes.c_void_p]
        self.lib.cn_reset.argtypes=[ctypes.c_void_p]; self.lib.cn_reset.restype=ctypes.c_int
        self.lib.cn_set_strength.argtypes=[ctypes.c_void_p,ctypes.c_float]; self.lib.cn_set_strength.restype=ctypes.c_int
        self.lib.cn_last_vad.argtypes=[ctypes.c_void_p]; self.lib.cn_last_vad.restype=ctypes.c_float
        self.handle=self.lib.cn_create(RATE,1,strength)
        if not self.handle: raise ValueError('Invalid engine configuration or allocation failure')
    def close(self):
        if self.handle: self.lib.cn_destroy(self.handle); self.handle=None
    def __enter__(self): return self
    def __exit__(self,*_): self.close()
    def reset(self):
        if not self.handle or self.lib.cn_reset(self.handle): raise ValueError('Engine is closed')
    def process(self,samples):
        if not self.handle: raise ValueError('Engine is closed')
        n=len(samples); x=(ctypes.c_float*n)(*samples); y=(ctypes.c_float*n)()
        if self.lib.cn_process(self.handle,x,y,n): raise ValueError('Expected finite float samples in [-1,1]')
        return list(y)
    def offline(self,samples,block=480):
        if block < 1: raise ValueError('block must be positive')
        self.reset(); padded=list(samples)+[0.0]*self.latency; out=[]
        for i in range(0,len(padded),block): out.extend(self.process(padded[i:i+block]))
        return out[self.latency:self.latency+len(samples)]
def read_wav(path):
    from ab_wav import load_wav
    wav = load_wav(path)
    if wav['channels'] != 1 or wav['sample_rate'] != RATE:
        raise ValueError('Expected WAV PCM16/Float32, mono, 48000 Hz; convert explicitly with ffmpeg')
    samples = wav['samples'][0]
    if any(abs(x) > 1 for x in samples):
        raise ValueError('Expected finite normalized WAV samples in [-1,1]')
    return samples
def write_wav(path,samples):
    data=array.array('h',(max(-32768,min(32767,round(x*32768))) for x in samples))
    if sys.byteorder!='little': data.byteswap()
    with wave.open(str(path),'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(RATE); f.writeframes(data.tobytes())
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('input',type=Path); p.add_argument('output',type=Path)
    p.add_argument('--library',type=Path,required=True)
    p.add_argument('--strength',type=float,default=1.0); p.add_argument('--block',type=int,default=480)
    a=p.parse_args()
    latency=LATENCY; library=a.library; strength=a.strength
    if a.input.resolve()==a.output.resolve(): p.error('Input and output must be different files')
    if a.output.exists(): p.error('Output already exists; choose a new path')
    try:
        samples=read_wav(a.input); start=time.perf_counter()
        with Engine(strength,library=library,latency=latency) as e: result=e.offline(samples,a.block)
        elapsed=time.perf_counter()-start; write_wav(a.output,result)
    except (OSError,ValueError,wave.Error) as exc: p.exit(2,str(exc)+'\n')
    print(json.dumps({'samples':len(samples),'seconds':len(samples)/RATE,'elapsed_seconds':elapsed,
                     'real_time_factor':elapsed/(len(samples)/RATE) if samples else None,
                     'nominal_stream_delay_samples':latency,'offline_delay_compensated':True,
                     'preset':'speexdsp-1.2.1-minus15','strength':strength}))
if __name__=='__main__': main()
