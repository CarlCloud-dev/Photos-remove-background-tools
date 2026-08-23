"""Optional CUDA runtime installed beside the Windows application.

The release backend is intentionally built with CPU PyTorch.  A CUDA wheel
cannot be swapped into a running frozen Python process, so this module installs
the official PyTorch CUDA wheels in ``<install>\\runtime\\cuda`` and makes them
the first import location on the *next* backend launch.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from typing import Dict, Generator, Iterable, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen


CUDA_TAG = "cu126"
TORCH_VERSION = "2.6.0"
TORCHVISION_VERSION = "0.21.0"
MIN_FREE_BYTES = 8 * 1024 * 1024 * 1024


def _app_root() -> Path:
    explicit = os.environ.get("REMOVE_BG_ROOT")
    if explicit:
        return Path(explicit).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parents[2]


def _runtime_root() -> Path:
    return _app_root() / "runtime" / "cuda"


def _site_packages() -> Path:
    return _runtime_root() / "site-packages"


def _manual_wheels_dir() -> Path:
    return _runtime_root() / "manual-wheels"


def _runtime_ready() -> bool:
    site_packages = _site_packages()
    return (site_packages / "torch" / "__init__.py").is_file() and (site_packages / "torch" / "lib" / "torch_cuda.dll").is_file()


def _python_tag() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def _wheel_names() -> tuple[str, str]:
    tag = _python_tag()
    platform = "win_amd64"
    return (
        f"torch-{TORCH_VERSION}+{CUDA_TAG}-{tag}-{tag}-{platform}.whl",
        f"torchvision-{TORCHVISION_VERSION}+{CUDA_TAG}-{tag}-{tag}-{platform}.whl",
    )


def _source_url(source: str, filename: str) -> str:
    if source == "domestic":
        return f"https://mirrors.aliyun.com/pytorch-wheels/{CUDA_TAG}/{filename}"
    # PyTorch's CDN expects the local-version '+' in wheel filenames to be
    # percent-encoded; leaving it raw responds with HTTP 403 on some CDNs.
    return f"https://download.pytorch.org/whl/{CUDA_TAG}/{filename.replace('+', '%2B')}"


def _gpu_probe() -> dict:
    """Detect NVIDIA hardware without importing the CPU-only torch package."""
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True,
            check=False,
            creationflags=flags,
            text=True,
            timeout=4,
        )
        row = result.stdout.strip().splitlines()[0] if result.returncode == 0 and result.stdout.strip() else ""
        if row:
            name, _, driver = row.partition(",")
            return {"gpu_detected": True, "gpu_name": name.strip(), "driver_version": driver.strip()}
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    return {"gpu_detected": False, "gpu_name": None, "driver_version": None}


def runtime_info() -> dict:
    site_packages = _site_packages()
    ready = _runtime_ready()
    root = _runtime_root()
    # Older releases kept the downloaded wheel archives after a successful
    # installation. They are not needed at runtime and can consume several GB.
    if ready:
        shutil.rmtree(root / "downloads", ignore_errors=True)
    disk = shutil.disk_usage(_app_root())
    return {
        **_gpu_probe(),
        "runtime_ready": ready,
        "runtime_dir": str(root),
        "site_packages_dir": str(site_packages),
        "manual_wheels_dir": str(_manual_wheels_dir()),
        "manual_wheels_ready": all((_manual_wheels_dir() / name).is_file() for name in _wheel_names()),
        "required_wheels": list(_wheel_names()),
        "sources": [
            {"id": "domestic", "name": "阿里云 PyTorch 国内镜像（自动下载默认）", "url": f"https://mirrors.aliyun.com/pytorch-wheels/{CUDA_TAG}/"},
            {"id": "global", "name": "PyTorch 官方 CUDA {CUDA_TAG} 轮子库", "url": f"https://download.pytorch.org/whl/{CUDA_TAG}"},
        ],
        "estimated_space_gib": 8,
        "free_space_gib": round(disk.free / 1024 / 1024 / 1024, 1),
    }


_DLL_DIRECTORY_HANDLES: list[object] = []


def activate_installed_cuda_runtime() -> bool:
    """Place a completed external runtime ahead of PyInstaller's CPU modules."""
    if not _runtime_ready():
        return False
    site_packages = str(_site_packages())
    if site_packages in sys.path:
        sys.path.remove(site_packages)
    sys.path.insert(0, site_packages)
    torch_lib = _site_packages() / "torch" / "lib"
    if hasattr(os, "add_dll_directory") and torch_lib.is_dir():
        _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(torch_lib)))
    return True


class RuntimeCancelled(Exception):
    pass


@dataclass
class _Progress:
    file_index: int = 0
    file_count: int = 0
    filename: str = ""
    current: int = 0
    total: Optional[int] = None
    queue: Queue = field(default_factory=Queue)

    def emit(self) -> None:
        if self.total and self.total > 0:
            fraction = min(1.0, self.current / self.total)
            percent = ((self.file_index + fraction) / max(1, self.file_count)) * 100
        else:
            percent = (self.file_index / max(1, self.file_count)) * 100
        self.queue.put({"event": "progress", "percent": percent, "filename": self.filename})


class CudaRuntimeDownloader:
    def __init__(self) -> None:
        self.root = _runtime_root()
        self.download_dir = self.root / "downloads"
        self.manual_wheels_dir = _manual_wheels_dir()
        self.site_packages = _site_packages()
        self.cancel_event = threading.Event()
        self.finished_event = threading.Event()
        self._response_lock = threading.Lock()
        self._response = None

    def cancel(self) -> None:
        self.cancel_event.set()
        with self._response_lock:
            response = self._response
        if response is not None:
            try:
                response.close()
            except OSError:
                pass

    def _raise_if_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise RuntimeCancelled("已取消 CUDA 运行时下载")

    def _download_wheel(self, filename: str, progress: _Progress) -> Path:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        part = self.download_dir / f"{filename}.part"
        target = self.download_dir / filename
        existing = part.stat().st_size if part.exists() else 0
        failures: list[str] = []
        for source in ("domestic", "global"):
            self._raise_if_cancelled()
            headers = {"User-Agent": "Photos-RMBG-tools/1.0"}
            if existing:
                headers["Range"] = f"bytes={existing}-"
            try:
                response = urlopen(Request(_source_url(source, filename), headers=headers), timeout=20)  # nosec B310 - fixed HTTPS sources
                with self._response_lock:
                    self._response = response
                status = getattr(response, "status", response.getcode())
                if existing and status != 206:
                    existing = 0
                    mode = "wb"
                else:
                    mode = "ab" if existing else "wb"
                length = response.headers.get("Content-Length")
                progress.current = existing
                progress.total = existing + int(length) if length and length.isdigit() else None
                progress.emit()
                with response, open(part, mode) as output:
                    while True:
                        self._raise_if_cancelled()
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        progress.current += len(chunk)
                        progress.emit()
                if progress.total and progress.current != progress.total:
                    raise OSError(f"{filename} 下载不完整")
                os.replace(part, target)
                return target
            except RuntimeCancelled:
                raise
            except (OSError, URLError, ValueError) as exc:
                failures.append(f"{source}: {exc}")
            finally:
                with self._response_lock:
                    self._response = None
        raise OSError("；".join(failures) or f"无法下载 {filename}")

    def _manual_wheels(self) -> list[Path]:
        wheels = [self.manual_wheels_dir / filename for filename in _wheel_names()]
        missing = [wheel.name for wheel in wheels if not wheel.is_file()]
        if missing:
            raise OSError(
                "手动安装文件不完整。请将以下文件直接放入 "
                + str(self.manual_wheels_dir)
                + "："
                + "、".join(missing)
            )
        invalid = [wheel.name for wheel in wheels if not zipfile.is_zipfile(wheel)]
        if invalid:
            raise OSError("手动安装文件不是有效 wheel：" + "、".join(invalid))
        return wheels

    @staticmethod
    def _extract_wheel(wheel: Path, target: Path, cancel_event: threading.Event) -> None:
        with zipfile.ZipFile(wheel) as archive:
            for member in archive.infolist():
                if cancel_event.is_set():
                    raise RuntimeCancelled("已取消 CUDA 运行时安装")
                destination = (target / member.filename).resolve()
                if os.path.commonpath((str(target.resolve()), str(destination))) != str(target.resolve()):
                    raise OSError("CUDA 轮子包含非法文件路径")
                archive.extract(member, target)

    def _install(self, wheels: Iterable[Path], progress: _Progress) -> None:
        stage = self.root / "site-packages.installing"
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True, exist_ok=True)
        try:
            for wheel in wheels:
                self._raise_if_cancelled()
                progress.queue.put({"event": "installing", "filename": wheel.name})
                self._extract_wheel(wheel, stage, self.cancel_event)
            manifest = {"torch": TORCH_VERSION, "torchvision": TORCHVISION_VERSION, "cuda": CUDA_TAG, "python": _python_tag()}
            (stage / "Photos-RMBG-tools-cuda-runtime.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            self._raise_if_cancelled()
            if self.site_packages.exists():
                shutil.rmtree(self.site_packages)
            os.replace(stage, self.site_packages)
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)

    def cleanup_partial(self) -> None:
        for directory in (self.download_dir, self.root / "site-packages.installing"):
            if directory.exists():
                shutil.rmtree(directory, ignore_errors=True)

    def cleanup_manual_wheels(self) -> None:
        if self.manual_wheels_dir.exists():
            shutil.rmtree(self.manual_wheels_dir, ignore_errors=True)

    def events(self, source: str = "automatic") -> Generator[str, None, None]:
        manual_install = source == "local"
        info = runtime_info()
        if info["runtime_ready"]:
            yield _sse({"event": "complete", "message": "CUDA 运行时已就绪。"})
            return
        if not info["gpu_detected"]:
            yield _sse({"event": "error", "message": "未检测到 NVIDIA GPU，无法启用 CUDA 加速。"})
            return
        if shutil.disk_usage(_app_root()).free < MIN_FREE_BYTES:
            yield _sse({"event": "error", "message": "安装 CUDA 运行时至少需要 8 GB 可用空间。"})
            return

        self.cancel_event.clear()
        self.finished_event.clear()
        progress = _Progress(file_count=len(_wheel_names()))
        result: list[Optional[BaseException]] = [None]

        installed = [False]

        def worker() -> None:
            try:
                if manual_install:
                    wheels = self._manual_wheels()
                    progress.queue.put({"event": "installing", "filename": "正在安装手动下载的 CUDA 运行时"})
                else:
                    wheels = []
                    for index, filename in enumerate(_wheel_names()):
                        progress.file_index = index
                        progress.filename = filename
                        wheels.append(self._download_wheel(filename, progress))
                self._install(wheels, progress)
                installed[0] = True
            except BaseException as exc:  # caller turns this into an SSE error
                result[0] = exc
            finally:
                # Auto-download wheels are pure installation cache. Always
                # remove them after a terminal result so runtime/site-packages
                # is the only retained CUDA copy. Manual wheels stay on an
                # install error to allow a retry, but are removed on success.
                if not manual_install or self.cancel_event.is_set():
                    self.cleanup_partial()
                if manual_install and installed[0]:
                    self.cleanup_manual_wheels()
                self.finished_event.set()
                _unregister(self)

        _register(self)
        thread = threading.Thread(target=worker, name="CudaRuntimeDownload", daemon=True)
        thread.start()
        last_emit = 0.0
        while not self.finished_event.is_set() or not progress.queue.empty():
            try:
                event = progress.queue.get(timeout=0.25)
                yield _sse(event)
                last_emit = time.monotonic()
            except Empty:
                if time.monotonic() - last_emit > 1.0:
                    yield _sse({"event": "progress", "percent": 0, "filename": progress.filename})
                    last_emit = time.monotonic()
        thread.join(timeout=2)
        if self.cancel_event.is_set() or isinstance(result[0], RuntimeCancelled):
            yield _sse({"event": "cancelled", "message": "已取消 CUDA 运行时下载，未完成文件已清理。"})
        elif result[0] is not None:
            yield _sse({"event": "error", "message": f"CUDA 运行时下载失败：{result[0]}"})
        elif runtime_info()["runtime_ready"]:
            yield _sse({"event": "complete", "message": "CUDA 运行时已安装，正在重启应用以启用 GPU。"})
        else:
            yield _sse({"event": "error", "message": "CUDA 运行时安装不完整。"})


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


_ACTIVE_LOCK = threading.Lock()
_ACTIVE: Optional[CudaRuntimeDownloader] = None


def _register(downloader: CudaRuntimeDownloader) -> None:
    global _ACTIVE
    with _ACTIVE_LOCK:
        _ACTIVE = downloader


def _unregister(downloader: CudaRuntimeDownloader) -> None:
    global _ACTIVE
    with _ACTIVE_LOCK:
        if _ACTIVE is downloader:
            _ACTIVE = None


def start_cuda_runtime_download(source: str = "automatic") -> Generator[str, None, None]:
    return CudaRuntimeDownloader().events(source=source)


def cancel_cuda_runtime_download() -> dict:
    with _ACTIVE_LOCK:
        downloader = _ACTIVE
    if downloader is None:
        CudaRuntimeDownloader().cleanup_partial()
        return {"active": False, "message": "没有正在进行的 CUDA 下载，已清理未完成文件。"}
    downloader.cancel()
    completed = downloader.finished_event.wait(timeout=5)
    if completed:
        downloader.cleanup_partial()
    return {"active": True, "completed": completed, "message": "正在停止 CUDA 下载。"}
