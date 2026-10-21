"""Compare independent surveyed Base targets; never fit or approve calibration.

Usage: python -m massage_controller.absolute_accuracy --measurements FILE
       --calibration FILE --output FILE
The report is numerical evidence for review, not a motion permission switch.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
from .geometry import camera_to_base_m


def vector(value):
    if (not isinstance(value,list) or len(value)!=3 or
            any(type(v) not in (int,float) or not math.isfinite(v) for v in value)):
        raise ValueError('坐标必须为三个有限数，单位 mm')
    return value


def evaluate(data, calibration, calibration_sha, evidence_root):
    blockers=[]
    if data.get('calibration_sha256')!=calibration_sha:
        blockers.append('测量记录的标定 SHA256 与当前文件不一致')
    if data.get('camera_serial')!=calibration['camera_serial']:
        blockers.append('测量记录的相机序列号不一致')
    if data.get('reference_frame')!=calibration['reference_frame']:
        blockers.append('测量记录的安装参考系不一致')
    reference=data.get('independent_reference',{})
    if reference.get('method') not in ('independent_survey','robot_probe_with_verified_tcp'):
        blockers.append('缺少独立测量方法；训练棋盘拟合结果不能用作独立真值')
    def evidence(name,label):
        if not isinstance(name,str) or not name or not (Path(evidence_root)/name).is_file():
            blockers.append(f'{label}证据文件不存在')
    evidence(reference.get('evidence_file'),'独立测量')
    uncertainty=reference.get('uncertainty_mm')
    if type(uncertainty) not in (int,float) or not math.isfinite(uncertainty) or uncertainty<0:
        blockers.append('缺少独立测量不确定度');uncertainty=None
    limit=data.get('acceptance',{}).get('max_error_mm')
    if type(limit) not in (int,float) or not math.isfinite(limit) or limit<=0:
        blockers.append('尚未确定本次用途的误差验收限值');limit=None
    records=[];seen=set()
    for point in data.get('measurements',[]):
        name=point.get('id')
        try:
            if not isinstance(name,str) or not name or name in seen:raise ValueError('标记点名称为空或重复')
            seen.add(name)
            target=vector(point.get('reference_base_mm'))
            camera=vector(point.get('camera_point_mm'))
            if camera[2]<=0:raise ValueError('相机深度必须为正')
            evidence(point.get('capture_evidence_file'),f'{name} 拍照')
            if point.get('capture_time_valid') is not True:
                blockers.append(f'{name} 的采集同步尚未通过')
            predicted=[x*1000 for x in camera_to_base_m(camera,point['T_base_reference'],calibration['T_reference_camera'])]
            delta=[a-b for a,b in zip(predicted,target)]
            error=math.sqrt(sum(x*x for x in delta))
            records.append(dict(id=name,reference_base_mm=target,vision_base_mm=predicted,
                delta_mm=delta,error_mm=error,upper_error_mm=error+uncertainty if uncertainty is not None else None))
        except (ValueError,KeyError,TypeError) as exc:
            blockers.append(f'{name or "未命名点"}: {exc}')
    # A commissioning sampling rule, not a universal accuracy standard.
    if len(records)<5: blockers.append('本次采样规则要求至少 5 个分布于目标区域的独立标记点')
    metrics=None
    if records:
        errors=[p['error_mm'] for p in records]
        xyz=[p['reference_base_mm'] for p in records]
        span=[max(p[i] for p in xyz)-min(p[i] for p in xyz) for i in range(3)]
        # Reject collinear/identical survey targets; never subtract a fitted bias.
        area=0.
        for a in xyz:
            for b in xyz:
                u=[x-y for x,y in zip(a,xyz[0])];v=[x-y for x,y in zip(b,xyz[0])]
                area=max(area,math.sqrt(sum(x*x for x in (
                    u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]))))
        if area<1: blockers.append('独立目标重合或共线，不能说明目标区域的精度')
        metrics=dict(mean_error_mm=sum(errors)/len(errors),max_error_mm=max(errors),
            rmse_mm=math.sqrt(sum(x*x for x in errors)/len(errors)),
            bias_xyz_mm=[sum(p['delta_mm'][i] for p in records)/len(records) for i in range(3)],
            measured_target_span_mm=span,
            max_error_plus_reference_uncertainty_mm=max(errors)+uncertainty if uncertainty is not None else None)
        if limit is not None and uncertainty is not None and max(errors)+uncertainty>limit:
            blockers.append('最大坐标误差加测量不确定度超过验收限值')
    return dict(status='INCOMPLETE_OR_FAILED' if blockers else 'NUMERIC_CHECK_PASSED_REVIEW_REQUIRED',
        calibration_sha256=calibration_sha,metrics=metrics,points=records,blockers=blockers,
        calibration_modified=False,robot_motion_approved=False,
        note='保留原始偏差，不重新拟合、不删除异常点；只评价实测覆盖区域，不自动批准运动')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--measurements',required=True);parser.add_argument('--calibration',required=True)
    parser.add_argument('--output',required=True);args=parser.parse_args()
    raw=Path(args.calibration).read_bytes();source=Path(args.measurements)
    report=evaluate(json.loads(source.read_text()),json.loads(raw),hashlib.sha256(raw).hexdigest(),source.parent)
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps(report,ensure_ascii=False,allow_nan=False))
    if report['blockers']: raise SystemExit(2)


if __name__=='__main__': main()
