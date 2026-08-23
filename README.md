# 智能抠图整合工具

面向 Windows 的本地 AI 抠图桌面工具。应用在本机处理图片，支持单图、批量抠图、前后效果对比、边缘优化、自动保存，以及 CPU / 可选 CUDA 推理。

## 功能

- 五种本地模型：U²-Net、RMBG-2.0、BiRefNet、BEN2、InSPyReNet
- 模型按需下载：界面提供国内镜像、国外官方来源和自动下载
- 输出固定为透明背景 PNG；结果名称会追加模型简称，例如 `photo_BiRefNet.png`
- 批量抠图自动保存到源图片同级的 `抠图结果` 文件夹
- 可选自动保存到源图目录；模型、日志、输出和 CUDA 运行时均保存于应用安装目录
- CPU 基础版可直接运行；选择 CUDA 后可按提示下载独立 GPU 运行时

## 模型与权重

本仓库不包含任何模型权重、模型缓存、CUDA 运行时或用户图片。首次使用某个模型时，请在应用内按提示选择国内镜像、国外官方页面或自动下载。

各模型及其权重分别受上游项目许可条款约束；使用、分发或再发布模型前，请自行阅读对应上游仓库和下载页面的许可说明。

## 开发环境

- Windows 10 / 11 x64
- Python 3.10–3.13
- Node.js 18+

首次准备后，开发模式运行：

```bat
build_all.bat
npm run dev
```

`build_all.bat` 会创建 `backend\venv`、安装依赖并准备 Python 后端；`npm run dev` 会启动 Vite、Electron 和本地后端。开发接口使用 `49173`，Vite 使用 `49174`。

## 构建安装版

关闭正在运行的应用后，在项目根目录执行：

```bat
build_all.bat
```

默认是增量构建。仅在 Python 或 Node 依赖发生变化时使用：

```bat
build_all.bat --full
```

安装程序生成在 `release/`。构建完成后会自动清理 `win-unpacked` 中间目录，只保留安装包。

## 数据位置与隐私

安装版将可变数据写入安装目录下的 `models`、`runtime`、`output`、`logs`、`profile` 和 `temp`，不会写入用户 AppData。图片推理在本机完成；联网仅用于用户主动选择的模型或 CUDA 运行时下载。

## 许可证

本项目源码采用 [MIT License](LICENSE)。第三方依赖和模型权重不因本许可证而改变其各自许可证。
