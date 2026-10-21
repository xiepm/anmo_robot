from setuptools import setup
from glob import glob
setup(name='massage_controller',version='0.1.0',packages=['massage_controller'],
      data_files=[('share/ament_index/resource_index/packages',['resource/massage_controller']),
                  ('share/massage_controller',['package.xml']),
                  ('share/massage_controller/config',glob('config/*')),
                  ('share/massage_controller/launch',glob('launch/*.py')),
                  ('share/massage_controller/rehab_protocol',glob('../../config/rehab_protocol/*.json'))],
      install_requires=['setuptools','websockets==12.0','jsonschema==4.23.0'],
      entry_points={'console_scripts':['control_node=massage_controller.node:main','vision_sim=massage_controller.vision_sim:main']})
