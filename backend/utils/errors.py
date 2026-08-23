"""错误码与异常定义。"""

from enum import IntEnum
from typing import Optional


class UserError(IntEnum):
    """面向用户的错误码。"""

    FILE_CORRUPTED = 1001
    OOM = 1002
    MODEL_MISSING = 1003
    UNSUPPORTED_FORMAT = 1004
    UPLOAD_TOO_LARGE = 1005
    MODEL_DOWNLOAD_FAILED = 1006
    DEPENDENCY_ERROR = 2001
    INTERNAL = 9999


class AppException(Exception):
    """应用层异常，携带用户可读错误码与消息。"""

    def __init__(
        self,
        code: int,
        user_message: str,
        inner: Optional[BaseException] = None,
    ) -> None:
        super().__init__(user_message)
        self.code = int(code)
        self.user_message = str(user_message)
        self.inner = inner

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.code}] {self.user_message}"


def from_exception(e: BaseException) -> AppException:
    """根据异常类型 / 消息关键字智能映射为用户友好的 AppException。

    同时兼容：Python 3.10 ~ 3.13+、torch 2.4 ~ 2.13+、Pillow 9.x/10.x、
    numpy 1.x/2.x、transformers 4.44 ~ 4.47 的差异。
    """
    msg = str(e).lower()
    cls_name = type(e).__name__.lower()
    tb = ""
    try:
        import traceback

        tb = traceback.format_exc().lower()
    except Exception:
        pass
    combined = f"{cls_name}\n{msg}\n{tb}"

    # ------------------------------------------------------------------
    # 1) 显存 / 内存 OOM
    # ------------------------------------------------------------------
    if any(k in combined for k in ("cuda out of memory", "out of memory", "oom")):
        return AppException(
            UserError.OOM.value,
            "显存不足，请关闭其他占用显存的程序后重试，或在设置中切换为仅 CPU 模式",
            inner=e,
        )

    # ------------------------------------------------------------------
    # 1.5) 推理张量与模型权重 dtype 不一致
    # ------------------------------------------------------------------
    # 自定义模型代码会被 Transformers 复制到 Hugging Face 的模块缓存；旧的
    # 下载关键词规则会因此把这类本地精度错误误判成“模型下载失败”。
    dtype_mismatch_kw = (
        "input type (float) and bias type (struct c10::half) should be the same",
        "expected scalar type half but found float",
        "expected scalar type float but found half",
    )
    if any(k in combined for k in dtype_mismatch_kw):
        return AppException(
            UserError.DEPENDENCY_ERROR.value,
            "模型推理精度不匹配，请重启应用后重试；若问题持续，请重新运行 build_all.bat 重建环境。",
            inner=e,
        )

    # ------------------------------------------------------------------
    # 2) TorchScript 在冻结包中无法读取第三方源码
    # ------------------------------------------------------------------
    if "torchscript requires source access" in combined or "could not get source code" in combined:
        return AppException(
            UserError.DEPENDENCY_ERROR.value,
            "内置模型依赖的源码未被完整打包，请使用修复后的 build_all.bat 重新构建软件。",
            inner=e,
        )

    # ------------------------------------------------------------------
    # 3) 图片损坏 / 无法解码
    # ------------------------------------------------------------------
    if any(
        k in combined
        for k in (
            "unidentifiedimageerror",
            "cannot identify image file",
            "truncated file",
            "image file is truncated",
            "cannot identify",
            "decoding error",
            "oserror",
        )
    ) and any(k in combined for k in ("image", "pil", "png", "jpg", "jpeg", "bmp", "webp", "truncat")):
        return AppException(
            UserError.FILE_CORRUPTED.value,
            "图片文件已损坏或无法读取，请更换其他图片",
            inner=e,
        )

    # ------------------------------------------------------------------
    # 3) 依赖缺失 / 不兼容（numpy2、torch、transformers、PIL API 变更）
    # ------------------------------------------------------------------
    dep_kw = (
        # AttributeError：常见 API 拼写/弃用
        "moduleattributeerror",
        "attributeerror",
        # Pillow 10+ 移除常量（Image.BILINEAR 等）
        "bilinear",
        "resampling",
        # numpy 2.x 被移除 / 改名的 API
        "numpy",
        "np.bool",
        "np.object",
        "np.float",
        "np.int",
        "axiserror",
        "dtype",
        # torch.nn 拼写错误 / 常量移除（nn.BILINEAR 等）
        "bilinear",
        "module 'torch.nn' has no attribute",
        # transformers / model 加载
        "trust_remote_code",
        "automodelforimagesegmentation",
        "safetensors_rust",
        # DataLoader num_workers 在 Windows spawn 下错误
        "num_workers",
        "freeze_support",
        "spawn",
    )
    if any(k in combined for k in dep_kw):
        # 进一步细分更精准的用户消息
        if "bilinear" in combined or (
            "attributeerror" in combined and any(k in combined for k in ("resampling", "torch.nn", "pil", "image"))
        ):
            return AppException(
                UserError.DEPENDENCY_ERROR.value,
                "运行环境版本不兼容（Pillow / PyTorch API 变更），请在项目根目录重新运行 build_all.bat 重新构建后端",
                inner=e,
            )
        if "numpy" in combined or any(k in combined for k in ("np.bool", "np.object", "np.float", "np.int")):
            return AppException(
                UserError.DEPENDENCY_ERROR.value,
                "NumPy 版本不兼容，请重新运行 build_all.bat 重建后端虚拟环境",
                inner=e,
            )
        if any(k in combined for k in ("freeze_support", "num_workers", "spawn")):
            return AppException(
                UserError.DEPENDENCY_ERROR.value,
                "Windows 多进程环境异常，请在设置中切换为「仅 CPU」模式后重试",
                inner=e,
            )
        if "trust_remote_code" in combined:
            return AppException(
                UserError.MODEL_MISSING.value,
                "模型代码加载失败，请确认网络通畅后重启软件重新下载模型",
                inner=e,
            )
        # 其它依赖类错误统一兜底
        return AppException(
            UserError.DEPENDENCY_ERROR.value,
            "运行依赖缺失或版本不兼容，请重新运行 build_all.bat 重建环境",
            inner=e,
        )

    # ------------------------------------------------------------------
    # 4) 模型文件缺失 / 权重找不到
    # ------------------------------------------------------------------
    model_missing_kw = (
        "no such file or directory",
        "filenotfounderror",
        "model.safetensors",
        "pytorch_model.bin",
        "config.json",
        "preprocessor_config.json",
    )
    if any(k in combined for k in model_missing_kw):
        if (
            "model" in msg
            or "config" in msg
            or any(k in combined for k in ("safetensors", ".bin", ".json"))
        ):
            return AppException(
                UserError.MODEL_MISSING.value,
                "模型文件缺失，请确认网络后重启软件自动下载",
                inner=e,
            )

    # ------------------------------------------------------------------
    # 5) 模型下载失败（网络、镜像不可达）
    # ------------------------------------------------------------------
    download_kw = (
        "connectionerror",
        "connection refused",
        "timeout",
        "huggingface",
        "snapshot_download",
        "404",
        "403",
        "500",
        "502",
        "503",
        "proxyerror",
        "sslerror",
    )
    if any(k in combined for k in download_kw):
        return AppException(
            UserError.MODEL_DOWNLOAD_FAILED.value,
            "模型下载失败，请检查网络连接后重试",
            inner=e,
        )

    # ------------------------------------------------------------------
    # 6) 写入权限不足（安装到 Program Files / 只读目录时常见）
    #    常见：OSError WinError 5=拒绝访问、PermissionError、EACCES/EPERM
    # ------------------------------------------------------------------
    fs_perm_kw = (
        "permission denied",
        "access is denied",
        "access denied",
        "eacces",
        "eperm",
        "拒绝访问",
        "没有权限",
        "winerror 5",
        "winerror 3",
        "failed to create process",
        "the system cannot find the path",
        "mkdir",
        "makedirs",
        "oserror",
    )
    _is_fs_permission = (
        isinstance(e, PermissionError)
        or isinstance(e, OSError)
        and any(k in combined for k in fs_perm_kw[:-1])  # exclude 'oserror' broad
        or any(k in combined for k in fs_perm_kw)
    )
    # Write-test failure: hint user to change cache dir / install elsewhere
    if _is_fs_permission and any(
        k in combined
        for k in ("mkdir", "makedirs", "output", "model", "cache", "log", "write", "save", "open")
    ):
        return AppException(
            UserError.DEPENDENCY_ERROR.value,
            "无写入权限：模型缓存或输出目录无法写入。若安装在 C 盘 Program Files，请重新安装到 D 盘等非系统目录，或点击右上角「设置」→「模型缓存目录」选择一个可写入的文件夹",
            inner=e,
        )
    # 其它权限错误降级为依赖类问题，附带原始msg
    if isinstance(e, PermissionError) or (
        isinstance(e, OSError) and any(k in combined for k in ("permission denied", "access is denied", "winerror 5", "拒绝访问"))
    ):
        return AppException(
            UserError.DEPENDENCY_ERROR.value,
            "操作系统拒绝访问（权限不足），请在设置中更改模型缓存目录，或重新运行 build_all.bat 构建后端",
            inner=e,
        )

    # ------------------------------------------------------------------
    # 7) Import / Module 缺失（requirements / PyInstaller hiddenimport 不全）
    #    - 缺少 kornia.xxx / transformers.models.xxx 这类动态子模块时，
    #      提示用户重新打包后端（通常是 hiddenimport 收窄导致漏包）。
    # ------------------------------------------------------------------
    if isinstance(e, ImportError) or "modulenotfounderror" in cls_name:
        err_msg = str(e).lower()
        # 常见漏包：kornia 子模块 / transformers 动态模型目录
        if any(k in err_msg for k in ("kornia.", "birefnet", "no module named 'kornia", "trust_remote_code")):
            return AppException(
                UserError.DEPENDENCY_ERROR.value,
                "打包后后端依赖模块不全，请重新运行 build_all.bat 完整重建后端 PyInstaller 产物",
                inner=e,
            )
        # 其它：通用依赖缺失
        return AppException(
            UserError.DEPENDENCY_ERROR.value,
            "缺少运行依赖，请重新运行 build_all.bat 安装 backend/requirements.txt",
            inner=e,
        )

    # ------------------------------------------------------------------
    # 默认兜底
    # ------------------------------------------------------------------
    return AppException(
        UserError.INTERNAL.value,
        "服务内部错误，请查看日志或联系开发者",
        inner=e,
    )
