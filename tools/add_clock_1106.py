import yaml, time
from pathlib import Path
from newsbar import extract, ingest
from newsbar.ocr.vision_backend import VisionBackend
cfg=yaml.safe_load(open('config/channels.1106.yaml'))['channels']
clocks={"tvbs":"11:59:33","ettv":"11:59:54","ftv":"11:59:46","cts":"11:59:44"}
b=VisionBackend()
for ch in cfg:
    t=time.time()
    p=Path(f'data/work_full/{ch}/obs.jsonl'); obs=extract.load_obs(p)
    n=extract.add_clock(obs, Path(f'data/raw/{ch}.ts'), cfg[ch], b, Path(f'data/work_full/{ch}'), ingest.sheet_offset(clocks[ch]))
    extract.save_obs(obs,p)
    print(ch,'clock calls',n,'missing secs',sum(1 for o in obs if o.clock_ok is False),round(time.time()-t),'s')
