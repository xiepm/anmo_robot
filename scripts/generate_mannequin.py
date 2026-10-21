#!/usr/bin/env python3
"""Generate original watertight mannequin meshes, no external asset downloads."""
from pathlib import Path
import math, struct
root=Path(__file__).resolve().parents[1]/'src/elfin_massage_sim/meshes'
root.mkdir(exist_ok=True)
def make(name,scale=(1,1,1),cap=None):
    n,m=48,24
    def vertex(j,i):
        lat=-math.pi/2+math.pi*j/m;lon=2*math.pi*i/n
        x=scale[0]*math.cos(lat)*math.cos(lon);y=scale[1]*math.cos(lat)*math.sin(lon);z=scale[2]*math.sin(lat)
        return (x,y,min(z,cap) if cap is not None else z)
    faces=[]
    for j in range(m):
        for i in range(n):
            a,b,c,d=vertex(j,i),vertex(j,i+1),vertex(j+1,i+1),vertex(j+1,i)
            if j>0:faces.append((a,b,d))
            if j<m-1:faces.append((b,c,d))
    with (root/name).open('wb') as f:
        f.write(b'Original parametric mannequin mesh'.ljust(80,b' '));f.write(struct.pack('<I',len(faces)))
        for a,b,c in faces:
            u=[b[k]-a[k] for k in range(3)];v=[c[k]-a[k] for k in range(3)]
            normal=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
            length=math.sqrt(sum(x*x for x in normal)) or 1
            f.write(struct.pack('<12fH',*[x/length for x in normal],*a,*b,*c,0))
make('ellipsoid.stl')
make('torso.stl',(.18,.30,.10),.07)
