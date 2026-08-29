# Smart Cutout Tool

[简体中文](README.md) | [English](README.en.md)

A local AI background-removal desktop app for Windows. Images are processed on your computer. It supports single-image and batch cutouts, before/after comparison, edge refinement, automatic saving, CPU inference, and optional CUDA inference. On the first launch, choose Simplified Chinese or English; the language can be changed later in Settings.

## Features

- Five local models: U²-Net, RMBG-2.0, BiRefNet, BEN2, and InSPyReNet.
- On-demand model download with a domestic mirror, official global sources, and automatic download.
- Results are transparent PNG files; the model tag is appended to the filename, for example `photo_BiRefNet.png`.
- Batch results are saved to a `抠图结果` folder beside the source images.
- Optional automatic saving next to the source image. Models, logs, outputs, and the CUDA runtime stay in the application installation folder.
- The base edition runs on CPU. When CUDA is selected, the app can download the required runtime automatically or install the two specified wheel files manually.

## Models and weights

This repository does not include model weights, model caches, the CUDA runtime, or user images. When a model is used for the first time, the app provides a domestic mirror, an official global page, and automatic download guidance.

Every model and its weights remain subject to its upstream licence. Review the corresponding upstream repository and download page before using, distributing, or republishing a model.

## Development environment

- Windows 10 / 11 x64
- Python 3.10–3.13
- Node.js 18+

After the first setup, run development mode with:

```bat
build_all.bat
npm run dev
```

`build_all.bat` creates `backend\venv`, installs dependencies, and prepares the Python backend. `npm run dev` starts Vite, Electron, and the local backend. The development backend uses port `49173`; Vite uses port `49174`.

## Build the installer

Close any running app, then run this command from the project root:

```bat
build_all.bat
```

The default build is incremental. Use a full build only after changing Python or Node dependencies:

```bat
build_all.bat --full
```

The installer is created in `release/`. After a successful build, the intermediate `win-unpacked` folder is removed and only the installer remains.

## Data location and privacy

The installed edition writes mutable data to `models`, `runtime`, `output`, `logs`, `profile`, and `temp` inside the installation directory. It does not use the user's AppData folder. Inference is local; network access is used only when the user chooses to download a model or CUDA runtime.

Running a newer installer detects the existing installation and upgrades it in place. Model weights, the CUDA runtime, plugins, settings, logs, and outputs are retained. Keep the original installation directory during an upgrade. To move the app, back up those folders and perform a new installation.

## Licence

The application source code is released under the [MIT License](LICENSE). Third-party dependencies and model weights retain their own licences.
