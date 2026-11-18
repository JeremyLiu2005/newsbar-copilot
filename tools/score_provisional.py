import json, sys
from newsbar.annotate import gold_cells, load
from newsbar.evaluate import evaluate, read_sheet
g=load(__import__('pathlib').Path('eval/gold/tvbs_researcher_provisional.json'))
gc=gold_cells(g['items'],['tvbs'])
gold={'tvbs':{m:c.value for m,c in gc['tvbs'].items()}}
sysd={'tvbs':read_sheet(sys.argv[1],{'tvbs':'TVBS-N'})['tvbs']}
for name,rng in [('全小時',None),('測試段 12:20–12:59',('1220','1259'))]:
    r=evaluate(sysd,gold,only_minutes=rng)
    print(name,'換題',r.change,'廣告',r.ad)
r=evaluate(sysd,gold)
print('label pairs:'); [print(' ',p['gold_minute'],p['gold'],'|',p['system']) for p in r.label_pairs]
print('errors:'); [print(' ',e) for e in r.errors]
