"""Stationary commissioning of ordinary STOP through the existing SDK owner.

No waypoint, enable, reset, output, or reconnect operations are available here.
Success is a dated observation of a stationary STOP, not proof of a moving stop.
"""
import copy
import time
import uuid

from .waypoint_plan import near


def initial_report(state='NONE'):
    return dict(state=state, check_id=uuid.uuid4().hex if state=='CHECKING' else '',
                stop_command_sent=False, stop_confirmed=False, checked_monotonic_s=None,
                samples_after_stop=0, start_pose=None, final_pose=None,
                reason='', execution_eligible=False)


def check_stop(reader, report, store, record, cancelled,
               clock=time.monotonic, sleep=time.sleep):
    result=copy.deepcopy(report)

    def read():
        sample=reader.sample()
        now=clock()
        if (not sample.get('valid') or not sample.get('connected') or
                sample.get('controller_simulation') is not False or
                any(not 0 <= now-sample[key] <= .5 for key in
                    ('host_monotonic_before_s','host_monotonic_after_s'))):
            raise ValueError('停止检查要求新鲜的真实反馈')
        sample['received_unix_ns']=time.time_ns()
        store(sample)
        state=sample['robot_state']
        if (not state['box_connected'] or any(state[k] for k in
                ('moving','paused','error','emergency_stop','safety_guard')) or
                not state['in_position'] or not state['blending_done']):
            raise ValueError('仅在机械臂停稳且无故障时检查普通停止通路')
        actual=sample['tcp_pose']['xyz_mm']+sample['tcp_pose']['rxyz_deg']
        return sample,actual

    try:
        if cancelled(): raise ValueError('检查已取消')
        sample,actual=read()
        result['start_pose']=actual
        for key in ('configured_tool_pose_mm_deg','configured_user_pose_mm_deg'):
            if not near(sample[key],[0.]*6,.01,.01):
                raise ValueError('停止检查要求零 TCP / Base UCS')
        if cancelled(): raise ValueError('检查已取消')
        record(dict(type='NONCONTACT_STOP_PREFLIGHT_COMMAND',check_id=result['check_id'],command='GrpStop'))
        result['stop_command_sent']=True
        reader.stop()  # One attempt only; an uncertain acknowledgment is not retried.
        after=clock()
        last=None
        while clock()-after <= 3:
            if cancelled(): raise ValueError('停止检查已取消，结果未确认')
            sample,actual=read()
            result['final_pose']=actual
            if not near(actual,result['start_pose'],.5,.2):
                raise ValueError('检查期间实际位置发生变化')
            stamp=sample['host_monotonic_before_s']
            if stamp>=after and (last is None or stamp>last):
                result['samples_after_stop']+=1
                last=stamp
            if result['samples_after_stop']>=3:
                result.update(state='PASSED',stop_confirmed=True,checked_monotonic_s=clock(),
                              reason='普通停止命令及连续 3 帧停稳反馈已验证；尚未验证运动中停止')
                break
            sleep(.1)
        else:
            raise ValueError('3 秒内未取得连续的新停止反馈')
    except Exception as exc:
        result.update(state='FAILED',reason=str(exc),checked_monotonic_s=clock())
    try:
        record(dict(type='NONCONTACT_STOP_PREFLIGHT_RESULT',**result))
    except Exception as exc:
        result.update(state='FAILED',reason='停止检查结果日志写入失败：'+str(exc))
    return result
