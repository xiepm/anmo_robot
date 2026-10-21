"""Render an existing stage-3 report without opening the camera or robot SDK."""
import argparse
import json
from pathlib import Path
import math

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Polygon, Rectangle


def render(source, output):
    data=json.loads(Path(source).read_text())
    summary,inputs,details=data['summary'],data['inputs'],data['details']
    if not details or not summary.get('kinematics_checked'):
        raise ValueError('报告中没有通过 SDK 复核的候选路线')
    scene=inputs['scene'];plan=details['candidate'];targets=summary['targets']
    offset=[targets[0]['tool_tip_base_mm'][i]-targets[0]['flange_base_mm'][i] for i in range(3)]
    route=[[p['x_mm']+offset[0],p['y_mm']+offset[1]] for p in
           [plan['start_pose']]+[point['pose'] for point in plan['points']]]
    nominal=[inputs['context']['start_flange_mm'][:2]]+[point['base_point_mm'][:2] for point in inputs['confirmation']['points']]
    plt.rcParams.update({'font.family':'Noto Sans CJK JP','axes.unicode_minus':False,'font.size':10})
    fig,ax=plt.subplots(figsize=(8,8.5),layout='constrained')
    radius=scene['corridor_radius_mm']
    for a,b in zip(nominal,nominal[1:]):
        dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy)
        if length:
            nx,ny=-dy/length*radius,dx/length*radius
            ax.add_patch(Polygon([[a[0]+nx,a[1]+ny],[b[0]+nx,b[1]+ny],
                                  [b[0]-nx,b[1]-ny],[a[0]-nx,a[1]-ny]],color='#eff2f5',zorder=1))
    for point in nominal:ax.add_patch(Circle(point,radius,color='#eff2f5',zorder=1))
    ax.plot(*zip(*route),color='#237bb8',linewidth=2,label='候选工具尖端中心线（XY 投影）',zorder=3)
    ax.scatter(*route[0],marker='s',color='#237bb8',zorder=4)
    ax.annotate('当前观察位',route[0],xytext=(-10,12),textcoords='offset points',ha='right')
    for i,p in enumerate(targets,1):
        xy=p['tool_tip_base_mm'][:2]
        name=('左' if p['side']=='LEFT' else '右' if p['side']=='RIGHT' else '')+p['name']
        ax.scatter(*xy,color='#ef7918',s=45,zorder=4)
        ax.annotate(f'{i} {name}',xy,xytext=(0,-19),textcoords='offset points',ha='center')
    for i,(x,y) in enumerate(scene['unknown_cell_examples_base_xy_mm']):
        size=scene['cell_size_mm']
        ax.add_patch(Rectangle((x-size/2,y-size/2),size,size,facecolor='#ffb0a8',
            edgecolor='#c63532',label='未知深度栅格' if not i else None,zorder=5))
    diag=summary['unknown_route_clearance']
    if diag['nearest_cell_base_xy_mm'] and diag['minimum_xy_distance_lower_bound_mm'] is not None:
        ax.annotate('最近未知栅格\n距中心线的平面保守下界\n'+f"{diag['minimum_xy_distance_lower_bound_mm']:.1f} mm",
            diag['nearest_cell_base_xy_mm'],xytext=(-660,-75),
            bbox=dict(boxstyle='round,pad=.5',fc='white',ec='#d2d8dd'),
            arrowprops=dict(arrowstyle='->',color='#c63532'),zorder=6)
    ax.set_aspect('equal');ax.set_xlabel('Base X / mm');ax.set_ylabel('Base Y / mm')
    ax.grid(alpha=.25,zorder=0);ax.legend(loc='lower right',fontsize=9)
    ax.set_title(f"阶段 3 · 未知深度位置图\n照片 {summary['frame_number']} · {summary['sample_count']} 个采样位置通过 SDK 复核",pad=15)
    all_xy=nominal+scene['unknown_cell_examples_base_xy_mm']
    ax.set_xlim(min(p[0] for p in all_xy)-65,max(p[0] for p in all_xy)+35)
    ax.set_ylim(min(p[1] for p in all_xy)-45,max(p[1] for p in all_xy)+75)
    fig.suptitle('灰色区域为 50 mm 分析带，不是已确认的自由空间。\n本图未核验实物外形、三维通路或相机盲区；不授予运动许可。',y=.055,fontsize=10)
    fig.get_layout_engine().set(rect=(0,.08,1,.92))
    fig.savefig(output,dpi=160);plt.close(fig)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('report');parser.add_argument('output')
    args=parser.parse_args();render(args.report,args.output)
