import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, Literal, Optional


def _app_root() -> str:
    """返回项目根目录。

    - PyInstaller onefile/onedir 打包时: 可执行文件所在目录的上一级(即项目根)
      若打包后 backend.exe 位于 dist/backend/backend.exe, 则其父级目录的父级为 dist,
      这里选择可执行文件所在目录(MEIPASS 或 exe 所在目录)作为基准, 并向上回到项目根。
    - 开发时: backend/ 所在目录的父级即项目根。
    """
    if getattr(sys, "frozen", False):
        # onedir: exe 目录在 dist/backend/, 回到 RemoveBG 根目录需要再向上两级
        # 若使用者换了部署方式, 可以通过环境变量 REMOVE_BG_ROOT 覆盖
        env_root = os.environ.get("REMOVE_BG_ROOT")
        if env_root:
            return os.path.abspath(env_root)
        # sys.executable: dist/backend/backend.exe
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        # exe_dir -> dist/backend, 父级 dist, 再父级 项目根
        candidate = os.path.dirname(os.path.dirname(exe_dir))
        # 如果不合法(比如直接运行了 dist/backend/backend.exe 脱离了项目), 退回到 exe 所在目录
        if os.path.basename(candidate) == "RemoveBG":
            return candidate
        return exe_dir
    # 开发模式: __file__ -> backend/config.py, 父级 backend, 再父级 项目根
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


APP_ROOT = _app_root()


def _install_path(name: str) -> str:
    """所有可变数据固定放在安装根目录，正式版不得回退到用户目录。"""
    return os.path.join(APP_ROOT, name)


def _enforce_install_paths(settings: "Settings") -> None:
    """忽略旧配置中的外部目录，避免模型或日志写入 AppData / C 盘。"""
    settings.MODEL_CACHE_DIR = _install_path("models")
    settings.LOG_DIR = _install_path("logs")
    settings.OUTPUT_DIR = _install_path("output")
    settings.PLUGIN_DIR = _install_path("plugins")


def _normalize_device(value: Any) -> Literal["cpu", "cuda"]:
    return "cuda" if str(value or "").strip().lower() == "cuda" else "cpu"


@dataclass
class Settings:
    APP_PORT: int = 49173
    APP_HOST: str = "127.0.0.1"
    MODEL_NAME: str = "briaai/RMBG-2.0"
    MODEL_CACHE_DIR: str = field(default_factory=lambda: _install_path("models"))
    DEVICE: Literal["cpu", "cuda"] = "cpu"
    LOG_DIR: str = field(default_factory=lambda: _install_path("logs"))
    OUTPUT_DIR: str = field(default_factory=lambda: _install_path("output"))
    PLUGIN_DIR: str = field(default_factory=lambda: _install_path("plugins"))

    def ensure_dirs(self) -> None:
        for name in ("MODEL_CACHE_DIR", "LOG_DIR", "OUTPUT_DIR", "PLUGIN_DIR"):
            path = getattr(self, name)
            if path and not os.path.exists(path):
                os.makedirs(path, exist_ok=True)


_default_settings = Settings()


def _default_config_path(path: Optional[str]) -> str:
    if path:
        return path
    # 安装版的配置必须与 exe 同级；不可写时由 Electron 主进程提示用户重新安装。
    return os.path.join(APP_ROOT, "config.json")


def _coerce_value(field_type, value):
    """简单的类型兼容: json 读回时把字段转成目标类型。"""
    try:
        if value is None:
            return None
        if field_type is int:
            return int(value)
        if field_type is str:
            return str(value)
        # Literal / 其他暂不强转
        return value
    except (TypeError, ValueError):
        return value


def load_settings(path: Optional[str] = None) -> Settings:
    config_path = _default_config_path(path)
    settings = Settings()
    if not os.path.exists(config_path):
        # 首次加载立即写一份默认配置, 便于使用者修改
        save_settings(settings, config_path)
        return settings

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # 配置文件损坏时退回默认值, 但仍保证目录存在
        return settings

    if not isinstance(data, dict):
        return settings

    type_map = {f.name: f.type for f in fields(settings)}
    for key, value in data.items():
        if hasattr(settings, key):
            setattr(settings, key, _coerce_value(type_map.get(key, type(value)), value))

    _enforce_install_paths(settings)
    settings.DEVICE = _normalize_device(settings.DEVICE)

    return settings


def save_settings(settings: Settings, path: Optional[str] = None) -> str:
    _enforce_install_paths(settings)
    settings.DEVICE = _normalize_device(settings.DEVICE)
    config_path = _default_config_path(path)
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(asdict(settings), f, ensure_ascii=False, indent=2)
    return config_path


def resolve_device(settings: Optional[Settings] = None) -> Literal["cuda", "cpu"]:
    """仅在用户明确选择 CUDA 且运行环境可用时启用 GPU。"""
    cfg = settings or _default_settings
    want = _normalize_device(cfg.DEVICE)
    if want == "cpu":
        return "cpu"
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        return "cpu"
    return "cpu"


def get_device_info(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """返回设置期望与 PyTorch 实际可用设备，供前端明确展示。"""
    cfg = settings or _default_settings
    requested = _normalize_device(cfg.DEVICE)
    info: Dict[str, Any] = {
        "requested_device": requested,
        "actual_device": "cpu",
        "cuda_available": False,
        "torch_version": None,
        "cuda_build": None,
        "gpu_name": None,
        "gpu_count": 0,
        "fallback_reason": None,
    }

    try:
        import torch  # type: ignore

        cuda_available = bool(torch.cuda.is_available())
        info.update(
            {
                "cuda_available": cuda_available,
                "torch_version": str(torch.__version__),
                "cuda_build": torch.version.cuda,
                "gpu_count": int(torch.cuda.device_count()) if cuda_available else 0,
            }
        )
        if cuda_available:
            info["gpu_name"] = str(torch.cuda.get_device_name(0))
    except Exception as exc:
        info["fallback_reason"] = f"PyTorch CUDA 检测失败：{exc.__class__.__name__}"
        return info

    if requested == "cuda" and info["cuda_available"]:
        info["actual_device"] = "cuda"
    elif requested == "cuda":
        info["fallback_reason"] = "当前 PyTorch 未检测到可用 CUDA，已回退至 CPU"
    return info
