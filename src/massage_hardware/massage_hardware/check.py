"""Local device inventory only: does not scan networks or contact a cabinet."""
import argparse,json,subprocess
from pathlib import Path
from ament_index_python.packages import get_package_share_directory,PackageNotFoundError
from .profile import load_profile,readiness_gaps

def usb_devices():
    result=[]
    for device in Path('/sys/bus/usb/devices').glob('*'):
        try:
            vendor=(device/'idVendor').read_text().strip();product=(device/'product').read_text().strip()
            if vendor=='8086' and ('realsense' in product.lower() or '435' in product):
                result.append({'name':product,'id':(device/'idProduct').read_text().strip(),
                    'serial':(device/'serial').read_text().strip() if (device/'serial').exists() else None,
                    'usb_speed_mbps':(device/'speed').read_text().strip()})
        except OSError:pass
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--profile',required=True);args=parser.parse_args()
    profile=load_profile(args.profile)
    try:driver=get_package_share_directory('realsense2_camera')
    except PackageNotFoundError:driver=None
    report={'mode':profile['mode'],'camera_driver_installed':driver is not None,
            'realsense_usb_devices':usb_devices(),'robot_adapter':profile['robot']['adapter'],
            'configuration_gaps':readiness_gaps(profile),'motion_enabled':False,
            'note':'USB inventory is not a streaming or calibration test. No robot connection or command attempted.'}
    print(json.dumps(report,ensure_ascii=False,indent=2))
