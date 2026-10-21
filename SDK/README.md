# 安装华沿机器人 SDK

仿真与普通单元测试不需要连接机械臂。使用真实机械臂前，需要自行从华沿机器人官方或设备供应商取得匹配控制器版本的 Python SDK；厂商包未随本仓库公开发布。

当前适配器对接的包名是 **HuayanRobotPythonSDK_V1.1.1.1**，入口为 `CPS.py`。解压后将文件放置为：

```text
SDK/
├── README.md
└── HUAYAN/
    └── python API接口/
        └── HuayanRobotPythonSDK_V1.1.1.1/
            └── CPS.py
```

如果采用其他目录，请修改自己的 `src/massage_controller/config/real_robot.json` 中的 `sdk_relative_path`；路径相对项目根目录。可从 [real_robot.example.json](../src/massage_controller/config/real_robot.example.json) 复制配置，但必须填写并核对控制器地址、机器人型号、关节映射和 SDK 版本。示例的 `model_confirmed=false` 需要完成实际核对后再修改。

```bash
cp src/massage_controller/config/real_robot.example.json \
   src/massage_controller/config/real_robot.json
```

以上只是安装文件，不会使能或移动机械臂。SDK 文件和本机配置已被 `.gitignore` 排除。

## 校验 SDK 适配

`src/massage_controller/test/test_noncontact_sdk_dispatch.py` 使用厂家字符串序列化器和假 I/O 验证运动参数，不创建 SDK 连接、不访问硬件；缺少厂商 SDK 时明确标记为跳过，安装后才执行这部分测试。其余纯逻辑测试使用测试替身。

厂商 C++、C#、Java SDK 和 PDF 手册属于可选参考资料，不是当前 Python 控制后端启动所必需。请保留厂家版权声明，并按厂家许可使用这些文件。
