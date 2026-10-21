from setuptools import setup
from glob import glob
setup(name='massage_hardware',version='0.1.0',packages=['massage_hardware'],
 data_files=[('share/ament_index/resource_index/packages',['resource/massage_hardware']),
 ('share/massage_hardware',['package.xml']),('share/massage_hardware/config',glob('config/*.yaml')),
 ('share/massage_hardware/launch',glob('launch/*.py'))],
 install_requires=['setuptools','PyYAML','numpy'],
 entry_points={'console_scripts':['hardware_console=massage_hardware.console:main',
                                 'hardware_check=massage_hardware.check:main']})
