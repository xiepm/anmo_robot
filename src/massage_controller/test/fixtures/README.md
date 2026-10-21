# Synthetic coordinate fixtures

These fixtures contain no measured robot or camera data and no valid device identity. Never use them as commissioning configuration.

The camera-to-reference transform is a +90° Z rotation followed by translation `(0.1, 0.2, 0.3)` metres. The reference-to-base transform is a +90° X rotation followed by translation `(0.4, -0.2, 0.5)` metres. Thus the camera point `(100, 200, 600)` mm becomes `(-0.1, 0.3, 0.9)` metres in the reference frame and `(300, -1100, 800)` mm in Base. The noncommuting rotations and translations check transform order, direction and mm/metre conversion without relying on deployment calibration.

The production parser's preview flags are present solely to exercise projection. All motion and absolute-accuracy approval flags are false.

`robot.synthetic.json` is a separate offline adapter fixture, loaded afresh through `robot_fixture.robot_config()`. It uses a reserved `.invalid` hostname and a nonexistent SDK path. Its model and joint-mapping flags describe only the fake readers used by unit tests; they do not approve any physical robot, calibration, or motion. Tests must not read a workstation's ignored `real_robot.json` or rely on operator edits to the public example.
