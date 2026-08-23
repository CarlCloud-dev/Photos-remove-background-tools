# 构建与发布说明

## 构建目标

项目仅生成 Windows x64 NSIS 安装版。基础安装包内置 CPU 推理依赖，不包含模型权重和 CUDA 运行时；两者均由用户在应用内按需下载并保存到安装目录。

## 必备环境

- Windows 10 / 11 x64
- Python 3.10–3.13，已加入 PATH
- Node.js 18+ 与 npm，已加入 PATH

可在新终端执行 `python --version`、`node --version`、`npm --version` 确认环境。

## 一键构建

在项目根目录执行：

```bat
build_all.bat
```

默认增量模式会复用已存在的 Python 虚拟环境、Node 依赖和后端打包产物，只在源码变化时重建必要部分。Python 或 Node 依赖更新后执行：

```bat
build_all.bat --full
```

构建流程依次检查运行环境、准备 Python 依赖、打包后端、构建渲染端，并创建 NSIS 安装程序。完整日志位于根目录 `build_all.log`。

## 产物

成功后，`release/` 仅保留：

```text
智能抠图整合工具-<version>-Setup-x64.exe
```

`win-unpacked` 是 electron-builder 生成安装程序时所需的临时目录，构建校验成功后会自动删除。

## 安装后的目录

安装程序会创建 `Photos-RMBG-tools` 目录。以下可变数据均写入安装目录，不使用 AppData：

```text
models/     模型权重，按模型分目录
runtime/    可选 CUDA 运行时
output/     应用缓存的处理结果
logs/       本地日志
profile/    应用设置
temp/       临时文件
```

## 发布前检查

1. 关闭正在运行的开发版与安装版应用，避免文件锁定。
2. 运行 `build_all.bat` 并确认安装程序存在。
3. 在干净环境中完成安装、首次启动、模型下载、CPU 抠图；如发布 CUDA 能力，再验证 CUDA 运行时按需安装。
4. 将安装程序上传至 GitHub Releases；不要把模型权重、`models/`、`runtime/`、`output/`、`logs/`、`release/` 或 `electron/resources/backend/` 提交到 Git。
5. 升级测试应在已有模型和 CUDA 运行时的安装目录上直接运行新版安装包，确认程序更新后 `models/`、`runtime/`、`profile/` 等目录仍存在。

## 常见问题

- 若首次启动尚未完成本地后端初始化，应用会显示加载层；服务可用后才允许开始抠图。
- 若模型不存在，点击模型状态标签或开始抠图会打开模型下载引导。
- 若构建失败，请先查看 `build_all.log`；依赖变化后再使用 `build_all.bat --full`。
