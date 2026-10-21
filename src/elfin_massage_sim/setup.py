from setuptools import setup
from glob import glob
setup(name='elfin_massage_sim', version='0.1.0', packages=['elfin_massage_sim'],
      data_files=[('share/ament_index/resource_index/packages', ['resource/elfin_massage_sim']),
                  ('share/elfin_massage_sim', ['package.xml']),
                  ('share/elfin_massage_sim/config', glob('config/*.rviz')),
                  ('share/elfin_massage_sim/meshes', glob('meshes/*.stl')),
                  ('share/elfin_massage_sim/worlds', ['worlds/massage.world']),
                  ('share/elfin_massage_sim/launch', ['launch/sim.launch.py'])],
      install_requires=['setuptools', 'numpy', 'scipy', 'python-fcl==0.7.0.8'], zip_safe=True,
      entry_points={'console_scripts': ['click_camera = elfin_massage_sim.click_camera:main',
                                       'demo_camera = elfin_massage_sim.demo_camera:main',
                                       'massage_hmi = elfin_massage_sim.hmi:main']})
