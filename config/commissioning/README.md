# 设备现场配置与示例

`*.example.json` 仅描述字段和当前软件支持的工作方式，不包含现场授权。将所需模板复制为本地配置并完成核对，再按安装说明指定文件路径。

| 模板 | 必须填写或核对的内容 |
| --- | --- |
| [noncontact.example.json](noncontact.example.json) | 假人/工装无接触模式、实际工具伸出量、间隙、速度与运行条件。当前实现只支持法兰 +Z 伸出 20 mm 的工具模型；模板中的数值是软件假设，不能当作对实际工具的测量。 |
| [waypoints.example.json](waypoints.example.json) | 独立逐点调试的真实起点、工具定义、目标点、允许范围及已核对的通路。空值和 `path_confirmed=false` 会阻止运行。 |
| [absolute_accuracy_measurements.example.json](absolute_accuracy_measurements.example.json) | 本机标定文件哈希、相机身份、独立参考测量方法、不确定度、误差限值及至少 5 个独立测量点。 |
| [capture_timing.example.json](capture_timing.example.json) | 对应真实相机的曝光元数据配置；默认 `verified=false`、序列号为空。仅核对本机曝光单位、固件、SDK、帧率后才可登记。 |

`noncontact.json` 是项目原有无接触算法参数，不包含某次照片的坐标和授权。新设备优先使用另存的 `noncontact.local.json`；软件根据本次照片和实时机器人反馈生成路线，现场核对仍对应这一条当前路线。

`*.authorization.json`、`waypoints.preview.json` 和本机实测配置由 Git 忽略。授权绑定当前照片、坐标版本和时效，不能从旧电脑复制或通过把模板中的布尔值改成 `true` 来替代页面确认。

`waypoints.example.json` 对应单独的调试工具，不是四个网页服务方案的穴位配置。网页方案定义在 `web/src/data/plans.ts` 和 `web/src/data/points.ts`。

控制与视觉共同读取 `capture_timing.local.json`，可通过 `REHAB_CAPTURE_TIMING` 指定其他绝对路径。当前曝光实现仅支持 D435i、固件 `5.15.1.55`、SDK `2.58.4`、30 fps、RGB 100 微秒/单位、深度 1 微秒/单位、RGB 手动曝光 1–300 的已实现模型。配置文件不能放宽这些门槛；其他型号/版本需要单独验证和适配。缺失或未核对配置时画面仍可预览，但不会宣称采集时间有效。曝光登记不等于标定精度验收或运动授权。
