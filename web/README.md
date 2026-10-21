> 当前完整安装与运行入口请先阅读[发布版说明](../docs/OPERATIONS.md)。本文保留模块开发和历史接口细节，涉及原工作站路径或阶段记录时，以根目录 README 和 docs 为准。

# Vue 3 + TypeScript + Vite

## 实机三进程启动（2026-10-21）

电脑重启并登录后，以普通用户打开终端运行：

```bash
bash /home/xpm/rehabilitation/启动系统.sh
```

脚本依次启动控制、视觉、网页三个用户服务；然后打开 <http://127.0.0.1:5173/>。
关闭终端不影响服务。重复运行复用已经在运行的服务，不重启控制、不自动恢复旧路线或发起运动。重启电脑后需再次运行此脚本；未设置开机自动启动机械臂控制。

```bash
bash /home/xpm/rehabilitation/启动系统.sh --status
```

日志位于 `web/reports/services/control.log`、`vision.log`、`web.log`。
实际进程入口为 `scripts/start_control.sh --backend real --standalone --enable-real-scan --noncontact-config config/commissioning/noncontact.json`、`web/scripts/start_vision_preview.sh`、`scripts/start_web.sh`。不要同时启动其他 RealSense 采集程序；`start_hmi.sh` 属于仿真流程。

本机有线网卡 `enp2s0` 使用 NetworkManager 持久连接 `rehabilitation-robot`：`192.168.0.20/24`，连接机械臂 `192.168.0.10`，不设置默认网关，不改变 Wi-Fi 上网。启动脚本本身不修改网卡或系统权限。

网页启动使用已有 `web/dist` 构建。UI 源码更新后先重新构建，再刷新浏览器（通过 node 调用，避免依赖文件执行权限）：

```bash
cd /home/xpm/rehabilitation/web
node node_modules/vue-tsc/bin/vue-tsc.js -b
node node_modules/vite/bin/vite.js build
```

首次缺少 `dist/index.html` 时启动脚本会构建；相机识别模型初始化期间页面可先打开，实时画面就绪后自动显示。

This template should help get you started developing with Vue 3 and TypeScript in Vite. The template uses Vue 3 `<script setup>` SFCs, check out the [script setup docs](https://v3.vuejs.org/api/sfc-script-setup.html#sfc-script-setup) to learn more.

Learn more about the recommended Project Setup and IDE Support in the [Vue Docs TypeScript Guide](https://vuejs.org/guide/typescript/overview.html#project-setup).
