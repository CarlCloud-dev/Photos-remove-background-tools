"""模型下载服务：基于 ModelScope 官方文件接口的 SSE 进度事件流。"""

import json
import logging
import os
import queue as _queue
import threading
import time
from typing import Dict, Generator, List, Optional
from urllib.request import Request, urlopen

from backend.config import APP_ROOT
from backend.utils.errors import AppException, UserError


# 跨线程传递 SSE 事件队列（_Bar 写入 -> 生成器线程读取）
_PENDING_SSE: "_queue.Queue[str]" = _queue.Queue()

# 优先使用国内可直连镜像。每个模型只下载实际推理必须的文件；不下载 README、
# 演示图、ONNX 导出或重复权重。ModelScope 模型仍使用其官方文件接口，其他
# 上游尚无 ModelScope 镜像的模型使用可访问的国内加速源。
MODELSCOPE_ENDPOINT = "https://modelscope.cn"
MODELSCOPE_REVISION = "master"

MODEL_SPECS: Dict[str, Dict[str, object]] = {
    "u2net": {
        "label": "U²-Net",
        "cache_subdir": "u2net",
        "repo_id": "iic/cv_u2net_salient-detection",
        "model_page": "https://modelscope.cn/models/iic/cv_u2net_salient-detection",
        "download_sources": (
            {
                "id": "domestic",
                "title": "国内下载",
                "name": "ModelScope 国内镜像（自动下载默认）",
                "url": "https://modelscope.cn/models/iic/cv_u2net_salient-detection",
            },
            {
                "id": "global",
                "title": "国外官方",
                "name": "U²-Net 官方项目（原始权重格式）",
                "url": "https://github.com/xuebinqin/U-2-Net",
            },
        ),
        "required_files": ("configuration.json", "pytorch_model.pt"),
    },
    "rmbg20": {
        "label": "RMBG-2.0",
        "cache_subdir": "rmbg20",
        "repo_id": "briaai/RMBG-2.0",
        "model_page": "https://modelscope.cn/models/briaai/RMBG-2.0/files",
        "download_sources": (
            {
                "id": "domestic",
                "title": "国内下载",
                "name": "ModelScope 国内镜像（自动下载默认）",
                "url": "https://modelscope.cn/models/briaai/RMBG-2.0/files",
            },
            {
                "id": "global",
                "title": "国外官方",
                "name": "Hugging Face 官方仓库",
                "url": "https://huggingface.co/briaai/RMBG-2.0/tree/main",
            },
        ),
        "required_files": (
            "config.json",
            "preprocessor_config.json",
            "birefnet.py",
            "BiRefNet_config.py",
            "model.safetensors",
        ),
        "legacy_dirs": ("models--briaai--RMBG-2.0",),
    },
    "birefnet": {
        "label": "BiRefNet",
        "cache_subdir": "birefnet",
        "repo_id": "ZhengPeng7/BiRefNet",
        "model_page": "https://hf-mirror.com/ZhengPeng7/BiRefNet/tree/main",
        "source_name": "Hugging Face 国内镜像（自动下载默认）",
        "source_url": "https://hf-mirror.com/ZhengPeng7/BiRefNet/tree/main",
        "download_sources": (
            {
                "id": "domestic",
                "title": "国内下载",
                "name": "Hugging Face 国内镜像（自动下载默认）",
                "url": "https://hf-mirror.com/ZhengPeng7/BiRefNet/tree/main",
            },
            {
                "id": "global",
                "title": "国外官方",
                "name": "Hugging Face 官方仓库",
                "url": "https://huggingface.co/ZhengPeng7/BiRefNet/tree/main",
            },
        ),
        "required_files": (
            "config.json",
            "BiRefNet_config.py",
            "birefnet.py",
            "model.safetensors",
        ),
        # ModelScope 暂无同源官方模型仓；这里使用国内 Hugging Face 镜像，
        # 仍下载 ZhengPeng7 官方仓中的原始代码与权重。
        "download_urls": {
            "config.json": "https://hf-mirror.com/ZhengPeng7/BiRefNet/resolve/main/config.json",
            "BiRefNet_config.py": "https://hf-mirror.com/ZhengPeng7/BiRefNet/resolve/main/BiRefNet_config.py",
            "birefnet.py": "https://hf-mirror.com/ZhengPeng7/BiRefNet/resolve/main/birefnet.py",
            "model.safetensors": "https://hf-mirror.com/ZhengPeng7/BiRefNet/resolve/main/model.safetensors",
        },
    },
    "ben2": {
        "label": "BEN2",
        "cache_subdir": "ben2",
        "repo_id": "PramaLLC/BEN2",
        "model_page": "https://modelscope.cn/models/PramaLLC/BEN2/files",
        "download_sources": (
            {
                "id": "domestic",
                "title": "国内下载",
                "name": "ModelScope 国内镜像（自动下载默认）",
                "url": "https://modelscope.cn/models/PramaLLC/BEN2/files",
            },
            {
                "id": "global",
                "title": "国外官方",
                "name": "Hugging Face 官方仓库",
                "url": "https://huggingface.co/PramaLLC/BEN2/tree/main",
            },
        ),
        "required_files": ("BEN2.py", "BEN2_Base.pth"),
    },
    "inspyrenet": {
        "label": "InSPyReNet",
        "cache_subdir": "inspyrenet",
        "repo_id": "plemeri/transparent-background",
        "source_name": "GitHub 国内加速下载",
        "source_url": "https://ghproxy.net/https://github.com/plemeri/transparent-background/releases/download/1.2.12/ckpt_base.pth",
        "download_sources": (
            {
                "id": "domestic",
                "title": "国内下载",
                "name": "GitHub 国内加速（非镜像，自动下载默认）",
                "url": "https://ghproxy.net/https://github.com/plemeri/transparent-background/releases/download/1.2.12/ckpt_base.pth",
            },
            {
                "id": "global",
                "title": "国外官方",
                "name": "GitHub 官方 Release",
                "url": "https://github.com/plemeri/transparent-background/releases/download/1.2.12/ckpt_base.pth",
            },
        ),
        "required_files": ("ckpt_base.pth",),
        "download_urls": {
            "ckpt_base.pth": "https://ghproxy.net/https://github.com/plemeri/transparent-background/releases/download/1.2.12/ckpt_base.pth",
        },
    },
}
DEFAULT_MODEL_ID = "rmbg20"
_DOWNLOAD_LOGGER = logging.getLogger("app")
_ACTIVE_DOWNLOADS: Dict[str, "ModelDownloader"] = {}
_ACTIVE_DOWNLOADS_LOCK = threading.Lock()


class DownloadCancelled(RuntimeError):
    """由用户主动取消的下载；不应被记录为下载失败。"""


def get_model_spec(model_id: Optional[str]) -> Dict[str, object]:
    """返回模型配置；禁止未知模型悄悄回退到其他模型。"""
    normalized = str(model_id or DEFAULT_MODEL_ID).strip().lower()
    spec = MODEL_SPECS.get(normalized)
    if spec is None:
        raise ValueError(f"不支持的模型：{model_id}")
    return spec


def _queue_progress(tracker, shim, force: bool = False) -> None:
    """将下载进度限流写入 SSE 队列，避免 885 MB 权重产生大量内存事件。"""
    now = time.monotonic()
    last_emit = getattr(tracker, "_last_sse_emit_ts", 0.0)
    if not force and now - last_emit < 0.15:
        return
    sse = shim._progress_event_fn(
        percent=tracker.percent(),
        speed_MB_s=tracker.speed_mb_s(),
        file=tracker.filename or "model",
    )
    try:
        _PENDING_SSE.put_nowait(sse)
    except _queue.Full:  # pragma: no cover - 默认队列不设上限
        pass
    tracker._last_sse_emit_ts = now


class ModelDownloader:
    """模型下载器：检查就绪状态 + 以 SSE 方式产出下载进度。"""

    def __init__(self, settings, model_id: str = DEFAULT_MODEL_ID) -> None:
        self.model_id = str(model_id or DEFAULT_MODEL_ID).strip().lower()
        self.spec = get_model_spec(self.model_id)
        self.model_label = str(self.spec["label"])
        self._repo_id = str(self.spec["repo_id"])
        self._model_page = str(self.spec.get("model_page", ""))
        self._source_name = str(self.spec.get("source_name", "ModelScope 国内镜像"))
        self._source_url = str(self.spec.get("source_url", self._model_page))
        self._download_urls = dict(self.spec.get("download_urls", {}))
        self._required_files = tuple(self.spec["required_files"])
        self._cache_dir = os.path.abspath(settings.MODEL_CACHE_DIR)
        # 统一缓存结构：<模型缓存目录>/<模型 ID>。
        self._model_subdir = str(self.spec["cache_subdir"])
        self._local_dir = os.path.join(self._cache_dir, self._model_subdir)
        self._cancel_event = threading.Event()
        self._finished_event = threading.Event()
        self._state_lock = threading.Lock()
        self._active_response = None
        # 仅删除当前自动下载会话已经落盘的文件，手动放入的完整模型文件不会被误删。
        self._session_completed_targets = set()

    # ------------------------------------------------------------------
    def is_model_ready(self) -> bool:
        """只在同一模型目录含完整运行集时才认为模型已就绪。

        ``snapshot_download`` 被中断时经常先落下 config.json 或
        birefnet.py。旧实现只要发现任意一个文件就跳过下载，随后推理时才以
        "模型缺失"失败，且无法自动恢复。权重可使用 safetensors 或旧版 bin。
        """
        return self._find_complete_model_dir() is not None

    def _find_complete_model_dir(self) -> Optional[str]:
        """返回完整模型所在目录，并兼容先前版本的散落缓存目录。"""
        candidate_dirs: List[str] = [self._local_dir]
        legacy_subdir = self._repo_id.replace("/", "__")
        for base_dir in (self._cache_dir, APP_ROOT, os.path.join(APP_ROOT, "backend")):
            legacy_dir = os.path.join(base_dir, legacy_subdir)
            if legacy_dir not in candidate_dirs:
                candidate_dirs.append(legacy_dir)
        for legacy_name in tuple(self.spec.get("legacy_dirs", ())):
            for base_dir in (self._cache_dir, APP_ROOT):
                legacy_dir = os.path.join(base_dir, str(legacy_name))
                if legacy_dir not in candidate_dirs:
                    candidate_dirs.append(legacy_dir)

        for root in candidate_dirs:
            if not os.path.isdir(root):
                continue
            for dirpath, _dirnames, filenames in os.walk(root):
                if all(
                    name in filenames
                    and os.path.getsize(os.path.join(dirpath, name)) > 0
                    for name in self._required_files
                ):
                    return dirpath
        return None

    @property
    def local_dir(self) -> str:
        return self._local_dir

    @property
    def model_dir(self) -> str:
        """完整模型的实际目录；未完成下载时返回新下载目标目录。"""
        return self._find_complete_model_dir() or self._local_dir

    def manual_download_info(self) -> dict:
        """供前端下载提示使用的国内/国外来源、文件清单与保存位置。"""
        sources = [dict(item) for item in tuple(self.spec.get("download_sources", ()))]
        domestic = next((item for item in sources if item.get("id") == "domestic"), None)
        if domestic is None:
            domestic = {
                "id": "domestic",
                "title": "国内下载",
                "name": self._source_name,
                "url": self._source_url,
            }
            sources.insert(0, domestic)
        return {
            "model_id": self.model_id,
            "model_label": self.model_label,
            "source_name": str(domestic["name"]),
            "source_url": str(domestic["url"]),
            "download_sources": sources,
            "target_dir": self._local_dir,
            "required_files": list(self._required_files),
        }

    def request_cancel(self) -> None:
        """请求停止下载，并关闭当前 HTTP 响应以打断可能阻塞的读取。"""
        self._cancel_event.set()
        with self._state_lock:
            response = self._active_response
        if response is not None:
            try:
                response.close()
            except Exception:  # noqa: BLE001 - cancellation must not be blocked by a close error
                pass

    def wait_for_finish(self, timeout: float) -> bool:
        """等待后台下载线程释放文件句柄，供取消接口确认清理结果。"""
        return self._finished_event.wait(timeout=max(0.0, float(timeout)))

    def cleanup_incomplete_download(self) -> int:
        """删除此模型未完成的 .part 文件及本次会话刚完成的伴随文件。"""
        candidates = set(self._session_completed_targets)
        for file_path in self._required_files:
            target_path = self._target_path(file_path)
            candidates.add(target_path + ".part")

        removed = 0
        for candidate in candidates:
            if not self._is_safe_model_path(candidate):
                continue
            try:
                if os.path.isfile(candidate):
                    os.remove(candidate)
                    removed += 1
            except OSError as exc:
                _DOWNLOAD_LOGGER.warning("清理未完成模型文件失败：%s，原因：%s", candidate, exc)
        return removed

    def _target_path(self, file_path: str) -> str:
        return os.path.abspath(os.path.join(self._local_dir, *file_path.split("/")))

    def _is_safe_model_path(self, path: str) -> bool:
        """确保清理动作始终局限在当前模型专属缓存目录。"""
        try:
            return os.path.commonpath((self._local_dir, os.path.abspath(path))) == self._local_dir
        except ValueError:
            return False

    def _raise_if_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise DownloadCancelled("用户已取消模型下载")

    # ------------------------------------------------------------------
    def start_download(self, mirror: bool = True) -> Generator[str, None, None]:
        """从当前模型配置的国内优先下载源获取完整推理文件，并产出 SSE 进度。"""
        try:
            os.makedirs(self._local_dir, exist_ok=True)
        except OSError as exc:
            yield self._error_event(
                f"无法创建模型缓存目录：{exc}。请检查磁盘权限与空间。"
            )
            return

        result: List[Optional[BaseException]] = [None]
        tracker = _DownloadTracker()
        shim = _TqdmShim(tracker, ModelDownloader._progress_event)
        self._cancel_event.clear()
        self._finished_event.clear()
        self._session_completed_targets.clear()

        def _worker() -> None:
            try:
                for file_path in self._required_files:
                    self._raise_if_cancelled()
                    self._download_model_file(file_path, tracker, shim)
            except DownloadCancelled as exc:
                result[0] = exc
            except BaseException as exc:  # noqa: BLE001 - capture all
                result[0] = exc
            finally:
                if self._cancel_event.is_set():
                    self.cleanup_incomplete_download()
                self._finished_event.set()
                _unregister_active_download(self)

        _register_active_download(self)
        thread = threading.Thread(target=_worker, name="ModelDownload", daemon=True)
        thread.start()

        # 起步事件。注册和线程均已就绪，因此用户立即点击“取消下载”也能生效。
        yield self._progress_event(0.0, 0.0, "preparing")

        # 主线程：轮询队列 + 保活心跳
        idle_since = time.monotonic()
        heartbeat_interval = 10.0
        while thread.is_alive() or not _PENDING_SSE.empty():
            try:
                evt = _PENDING_SSE.get(timeout=0.2)
                yield evt
                idle_since = time.monotonic()
            except _queue.Empty:
                now = time.monotonic()
                if now - idle_since > heartbeat_interval:
                    # 心跳包：event=progress, percent 不变
                    yield self._progress_event(
                        tracker.percent(), tracker.speed_mb_s(),
                        tracker.filename or "downloading"
                    )
                    idle_since = now
                continue

        thread.join(timeout=3.0)

        # 用户取消：后台线程已经关闭连接、释放文件句柄并清理未完成文件。
        if self._cancel_event.is_set() or isinstance(result[0], DownloadCancelled):
            yield self._cancelled_event()
            return

        # 处理异常
        if result[0] is not None:
            exc = result[0]
            _DOWNLOAD_LOGGER.exception(
                "模型下载失败，source=%s，repo=%s，目标目录=%s",
                self._source_name,
                self._repo_id,
                self._local_dir,
                exc_info=exc,
            )
            # 关键字映射
            app_exc = AppException(
                UserError.MODEL_DOWNLOAD_FAILED.value,
                "模型下载失败，请检查网络连接后重试",
                inner=exc,
            )
            yield self._error_event(app_exc.user_message)
            return

        # 结束
        yield (
            "data: "
            + json.dumps(
                {
                    "event": "done",
                    "percent": 100,
                    "local_dir": self._local_dir,
                },
                ensure_ascii=False,
            )
            + "\n\n"
        )

    def _modelscope_file_url(self, file_path: str) -> str:
        """返回 ModelScope 对应文件的官方 repo 下载接口。"""
        return (
            f"{MODELSCOPE_ENDPOINT}/api/v1/models/{self._repo_id}/repo"
            f"?Revision={MODELSCOPE_REVISION}&FilePath={file_path}"
        )

    def _download_model_file(self, file_path: str, tracker, shim) -> None:
        """下载单个模型文件；非主动取消的中断可由 ``.part`` 文件续传。"""
        url = str(self._download_urls.get(file_path) or self._modelscope_file_url(file_path))
        self._download_url_file(file_path, url, tracker, shim)

    def _download_url_file(self, file_path: str, url: str, tracker, shim) -> None:
        """将可信配置中的 HTTPS 文件保存至统一模型目录。"""
        self._raise_if_cancelled()
        target_path = self._target_path(file_path)
        part_path = target_path + ".part"
        os.makedirs(os.path.dirname(target_path), exist_ok=True)

        # 完整模型在本地时不重复传输；完整性由 is_model_ready() 在全部文件层面判断。
        if os.path.isfile(target_path) and os.path.getsize(target_path) > 0:
            tracker.reset(total=os.path.getsize(target_path), filename=file_path)
            tracker.update(os.path.getsize(target_path))
            _queue_progress(tracker, shim)
            return

        existing_size = os.path.getsize(part_path) if os.path.isfile(part_path) else 0
        headers = {"User-Agent": "RemoveBG-Desktop/1.0"}
        if existing_size:
            headers["Range"] = f"bytes={existing_size}-"

        request = Request(url, headers=headers)
        try:
            # 连接阶段使用较短超时；主动取消时则会直接 close() 已建立的响应。
            response = urlopen(request, timeout=15)  # nosec B310 - fixed HTTPS endpoint
        except BaseException as exc:  # noqa: BLE001 - convert a concurrent close into cancellation
            if self._cancel_event.is_set():
                raise DownloadCancelled("用户已取消模型下载") from exc
            raise

        try:
            with self._state_lock:
                self._active_response = response
            self._raise_if_cancelled()
            with response:
                status = getattr(response, "status", response.getcode())
                # 某些 CDN 不支持 Range；此时从零重新写入，避免在完整文件后追加。
                if existing_size and status != 206:
                    existing_size = 0
                    mode = "wb"
                else:
                    mode = "ab" if existing_size else "wb"

                raw_total = response.headers.get("Content-Length")
                try:
                    response_size = int(raw_total) if raw_total else None
                except (TypeError, ValueError):
                    response_size = None
                total = existing_size + response_size if response_size is not None else None
                tracker.reset(total=total, filename=file_path)
                if existing_size:
                    tracker.update(existing_size)
                _queue_progress(tracker, shim)

                with open(part_path, mode) as output:
                    while True:
                        self._raise_if_cancelled()
                        try:
                            chunk = response.read(1024 * 1024)
                        except BaseException as exc:  # noqa: BLE001 - response.close() may raise here
                            if self._cancel_event.is_set():
                                raise DownloadCancelled("用户已取消模型下载") from exc
                            raise
                        self._raise_if_cancelled()
                        if not chunk:
                            break
                        output.write(chunk)
                        tracker.update(len(chunk))
                        _queue_progress(tracker, shim)
        finally:
            with self._state_lock:
                if self._active_response is response:
                    self._active_response = None

        self._raise_if_cancelled()
        if tracker.total is not None and tracker.current != tracker.total:
            raise OSError(
                f"模型下载不完整：{file_path}，期望 {tracker.total} 字节，实际 {tracker.current} 字节"
            )
        os.replace(part_path, target_path)
        self._session_completed_targets.add(target_path)
        _queue_progress(tracker, shim, force=True)

    # ------------------------------------------------------------------
    @staticmethod
    def _progress_event(percent: float, speed_MB_s: float, file: str) -> str:
        payload = {
            "event": "progress",
            "percent": round(float(percent), 2),
            "speed_MB_s": round(float(speed_MB_s), 3),
            "file": file,
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    @staticmethod
    def _error_event(message: str) -> str:
        payload = {"event": "error", "message": str(message)}
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    @staticmethod
    def _cancelled_event() -> str:
        payload = {"event": "cancelled", "message": "下载已取消，未完成文件已清理。"}
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _download_key(downloader: ModelDownloader) -> str:
    return f"{downloader._cache_dir}::{downloader.model_id}"


def _register_active_download(downloader: ModelDownloader) -> None:
    with _ACTIVE_DOWNLOADS_LOCK:
        _ACTIVE_DOWNLOADS[_download_key(downloader)] = downloader


def _unregister_active_download(downloader: ModelDownloader) -> None:
    with _ACTIVE_DOWNLOADS_LOCK:
        key = _download_key(downloader)
        if _ACTIVE_DOWNLOADS.get(key) is downloader:
            _ACTIVE_DOWNLOADS.pop(key, None)


def cancel_model_download(settings, model_id: str, wait_timeout: float = 4.0) -> dict:
    """取消活跃下载；无活跃任务时仅清理此模型留下的 .part 文件。"""
    probe = ModelDownloader(settings, model_id)
    with _ACTIVE_DOWNLOADS_LOCK:
        downloader = _ACTIVE_DOWNLOADS.get(_download_key(probe))

    if downloader is None:
        return {
            "active": False,
            "completed": True,
            "removed_files": probe.cleanup_incomplete_download(),
        }

    downloader.request_cancel()
    completed = downloader.wait_for_finish(wait_timeout)
    # 正常情况下 worker 已完成清理；再次调用仅处理没有文件句柄的残留，安全且幂等。
    removed = downloader.cleanup_incomplete_download() if completed else 0
    return {"active": True, "completed": completed, "removed_files": removed}


# ---------------------------------------------------------------------------
# 下载进度跟踪器 + Hub 的 tqdm 接口适配
# ---------------------------------------------------------------------------


class _DownloadTracker:
    def __init__(self) -> None:
        self.total: Optional[int] = None
        self.current: int = 0
        self.filename: str = ""
        self._start_ts: float = time.monotonic()
        self._last_bytes: int = 0
        self._last_ts: float = self._start_ts

    def reset(self, total: Optional[int] = None, filename: str = "") -> None:
        self.total = total if total and total > 0 else None
        self.current = 0
        if filename:
            self.filename = filename
        self._start_ts = time.monotonic()
        self._last_ts = self._start_ts
        self._last_bytes = 0

    def update(self, n: int) -> None:
        self.current += int(n)

    def percent(self) -> float:
        if not self.total:
            return 0.0
        p = self.current / self.total * 100.0
        return p if p <= 100.0 else 100.0

    def speed_mb_s(self) -> float:
        now = time.monotonic()
        elapsed = now - self._last_ts
        if elapsed <= 0:
            return 0.0
        delta = self.current - self._last_bytes
        self._last_bytes = self.current
        self._last_ts = now
        return delta / 1024.0 / 1024.0 / elapsed


class _TqdmShim:
    """huggingface_hub 的 ``tqdm_class`` 工厂。Hub 调用方式：
        with tqdm_class(total=..., desc=filename, ...) as bar: bar.update(n)
    """

    def __init__(self, tracker: _DownloadTracker, progress_event_fn) -> None:
        self._tracker = tracker
        self._progress_event_fn = progress_event_fn

    def __call__(self, *args, **kwargs):
        total = kwargs.get("total", None)
        desc = kwargs.get("desc", "") or ""
        self._tracker.reset(total=total, filename=desc)
        return _Bar(self._tracker, self._progress_event_fn)


class _Bar:
    """上下文管理器 + update，向全局队列写 SSE。"""

    def __init__(self, tracker: _DownloadTracker, progress_event_fn) -> None:
        self._tracker = tracker
        self._progress_event_fn = progress_event_fn
        self._last_emit_ts: float = time.monotonic()
        self._emit_interval: float = 0.12

    def update(self, n: int) -> None:
        self._tracker.update(n)
        now = time.monotonic()
        if now - self._last_emit_ts < self._emit_interval:
            return
        sse = self._progress_event_fn(
            percent=self._tracker.percent(),
            speed_MB_s=self._tracker.speed_mb_s(),
            file=self._tracker.filename or "model",
        )
        try:
            _PENDING_SSE.put_nowait(sse)
        except _queue.Full:  # pragma: no cover - 无限队列
            pass
        self._last_emit_ts = now

    def close(self) -> None:
        sse = self._progress_event_fn(
            percent=self._tracker.percent() or 100.0,
            speed_MB_s=0.0,
            file=self._tracker.filename or "model",
        )
        try:
            _PENDING_SSE.put_nowait(sse)
        except _queue.Full:  # pragma: no cover
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.close()
        except Exception:
            pass
        return False
