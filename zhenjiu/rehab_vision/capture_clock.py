"""Per-frame clock evidence for the measured D435i / Linux UVC setup.

RGB exposure control/metadata is in 100 us UVC units, depth exposure in us.
RGB SENSOR_TIMESTAMP is retained as evidence, not treated as a verified optical
center: on FW 5.15.1.55 its offset is half the unscaled UVC number. The control
side brackets both interpretations plus a frame of rolling-readout allowance.
"""
import time
from pathlib import Path
import sys

# Import only the dependency-free shared policy loader, never a robot client.
CONTROL_SOURCE = Path(__file__).resolve().parents[2] / 'src/massage_controller'
if str(CONTROL_SOURCE) not in sys.path:
    sys.path.insert(0, str(CONTROL_SOURCE))
from massage_controller.capture_policy import load_capture_policy, policy_identity
from .camera_diagnostics import frame_diagnostics

MODEL = 'D435I_MANUAL_RGB_INTERVAL_V1'


class CaptureClock:
    def __init__(self, rs, profile, exposure, fps):
        self.rs, self.saved, self.previous = rs, [], {}
        self.ready_at = time.monotonic()+2
        self.good = 0
        self.error = ''
        self.identity = {}
        self.exposure = exposure
        try:
            device = profile.get_device()
            self.identity = dict(camera_serial=device.get_info(rs.camera_info.serial_number),
                firmware=device.get_info(rs.camera_info.firmware_version),
                sdk_version=getattr(rs, '__version__', 'unknown'))
            policy = load_capture_policy()
            if (self.identity != policy_identity(policy)
                    or fps != policy['fps'] or not 1 <= exposure <= 300):
                raise ValueError('当前相机/固件/SDK/帧率不属于已核对的曝光配置')
            self.color = device.first_color_sensor()
            for option in (rs.option.enable_auto_exposure, rs.option.exposure):
                self.saved.append((option,self.color.get_option(option)))
            self.color.set_option(rs.option.enable_auto_exposure,0)
            self.color.set_option(rs.option.exposure,exposure)
            if self.color.get_option(rs.option.enable_auto_exposure)!=0 or self.color.get_option(rs.option.exposure)!=exposure:
                raise ValueError('RGB 手动曝光设置回读不一致')
        except (RuntimeError,ValueError) as exc:
            self.error=str(exc)
            self.restore()

    def diagnostics(self, color, depth):
        # In particular on Windows, these raw numbers are useful for a later
        # timing acceptance but are not an alternative to a verified model.
        return dict(platform=sys.platform, identity=dict(self.identity),
                    model_error=self.error, diagnostic_only=True,
                    color=frame_diagnostics(self.rs, color),
                    depth=frame_diagnostics(self.rs, depth))

    def restore(self):
        errors=[]
        for option,value in reversed(self.saved):
            try:self.color.set_option(option,value)
            except RuntimeError as exc:errors.append(str(exc))
        self.saved=[]
        return errors

    def frame(self, frame, kind):
        rs=self.rs
        if frame.get_frame_timestamp_domain()!=rs.timestamp_domain.global_time:
            raise ValueError('全局时钟尚未就绪')
        fields=('frame_timestamp','sensor_timestamp','actual_exposure','auto_exposure')
        missing=[k for k in fields if not frame.supports_frame_metadata(getattr(rs.frame_metadata_value,k))]
        if missing:raise ValueError(f'{kind} 曝光元数据缺失：{missing}')
        values={k:frame.get_frame_metadata(getattr(rs.frame_metadata_value,k)) for k in fields}
        if kind=='color' and (values['auto_exposure']!=0 or values['actual_exposure']!=self.exposure):
            raise ValueError('RGB 曝光未稳定在指定手动值')
        unit=100 if kind=='color' else 1
        return dict(domain='global_time',frame_number=frame.get_frame_number(),
            global_timestamp_ms=frame.get_timestamp(),frame_timestamp_us=values['frame_timestamp'],
            sensor_timestamp_us=values['sensor_timestamp'],actual_exposure_us=values['actual_exposure']*unit,
            exposure_raw=values['actual_exposure'],exposure_unit_us=unit,auto_exposure=values['auto_exposure'])

    def read(self, color, depth):
        if self.error:raise ValueError(self.error)
        before=time.monotonic();wall=time.time();after=time.monotonic()
        try:
            streams=dict(color=self.frame(color,'color'),depth=self.frame(depth,'depth'))
            for key,frame in streams.items():
                previous=self.previous.get(key)
                if previous:
                    dt=frame['global_timestamp_ms']-previous['global_timestamp_ms']
                    hw=(frame['frame_timestamp_us']-previous['frame_timestamp_us'])/1000
                    if frame['frame_number']<=previous['frame_number'] or not 0<dt<=1000 or abs(dt-hw)>5:
                        raise ValueError('重复帧、设备时钟回绕或全局时钟映射不稳定')
            self.previous=streams
            self.good+=1
            if after<self.ready_at or self.good<5:raise ValueError('等待曝光及全局时钟稳定')
            return dict(model=MODEL,**self.identity,host_monotonic_s=(before+after)/2,
                        host_unix_s=wall,clock_read_width_s=after-before,**streams)
        except ValueError as exc:
            if '等待曝光' not in str(exc):
                self.previous={};self.good=0;self.ready_at=after+2
            raise
