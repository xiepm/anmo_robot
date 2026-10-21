#!/usr/bin/env python3
"""Check the recorded actual RGB-D and TCP feedback of a full demonstration."""
import json,math
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'reports/demo'
d=json.loads((p/'massage-demo.json').read_text())
assert d['passed'] and d['controller_error_code']==0
expected=['OBSERVE']+[f'P{i}_{phase}' for i in range(1,4) for phase in ['PREAPPROACH','NORMAL_APPROACH','RELEASE','PRESS','RETREAT']]+['OBSERVE']
assert [s['stage'] for s in d['stages']]==expected
reference=[(.55,-.10,.40),(.65,-.02,.40),(.56,.11,.40)]
rows=[]
for i,(measured,ref) in enumerate(zip(d['measured_surface_points'],reference),1):
    projection=math.dist(measured,ref)
    target=[measured[0],measured[1],measured[2]+.02]
    approach=[s['tcp'] for s in d['samples'] if s['stage']==f'P{i}_NORMAL_APPROACH']
    press=[s['tcp'] for s in d['samples'] if s['stage']==f'P{i}_PRESS']
    assert len(approach)>5 and len(press)>5
    error=min(math.dist(t,target) for t in press)
    lateral=max(math.hypot(t[0]-target[0],t[1]-target[1]) for t in approach)
    assert projection<.01 and error<.003 and lateral<.003
    rows.append({'point':i,'surface_mapping_error_mm':projection*1000,'press_tcp_error_mm':error*1000,'normal_max_lateral_mm':lateral*1000})
result={'passed':True,'stages':len(expected),'actual_wall_seconds':d['samples'][-1]['t'],'tcp_samples':len(d['samples']),'points':rows}
(p/'validation.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
