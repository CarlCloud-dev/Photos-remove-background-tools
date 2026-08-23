"""本地抠图模型的推理服务（懒加载 + 设备管理 + 参数化后处理）。"""

import gc
import importlib.util
import os
import sys
import time
from typing import Any, Dict, Optional

from backend.config import resolve_device
from backend.services.alpha_matting_service import refine_alpha
from backend.services.download_service import DEFAULT_MODEL_ID, ModelDownloader, get_model_spec
from backend.utils.errors import AppException, UserError, from_exception
from backend.utils.logger import push_user_log

# 全局懒加载模型
model: Any = None
model_device: Optional[str] = None
active_model_id: Optional[str] = None

MODEL_INPUT_SIZES = {
    "u2net": 320,
    "rmbg20": 1024,
    "birefnet": 1024,
    "ben2": 1024,
    "inspyrenet": 1024,
}


def _resolve_input_size(model_id: str) -> int:
    """使用各模型官方示例的原生推理输入规格。"""
    return MODEL_INPUT_SIZES[model_id]


def build_transform(input_size: int):
    """RMBG-2.0 / BiRefNet 的官方预处理：Resize + ImageNet Normalize。"""
    from torchvision import transforms  # type: ignore

    return transforms.Compose(
        [
            transforms.Resize((input_size, input_size)),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225],
            ),
        ]
    )


def build_inspyrenet_transform(original, dynamic_resize: bool):
    """复刻 InSPyReNet 官方 static / dynamic 预处理；dynamic 保留更多高分辨率细节。"""
    from PIL import Image  # type: ignore
    from torchvision import transforms  # type: ignore

    image = original
    if dynamic_resize:
        width, height = image.size
        longest_short_side = 1280
        if width >= height and height > longest_short_side:
            width = width / (height / longest_short_side)
            height = longest_short_side
        elif height > width and width > longest_short_side:
            height = height / (width / longest_short_side)
            width = longest_short_side
        width = max(32, int(round(width / 32)) * 32)
        height = max(32, int(round(height / 32)) * 32)
        image = image.resize((width, height), Image.BILINEAR)
    else:
        image = image.resize((1024, 1024), Image.BILINEAR)

    return transforms.Compose(
        [
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225],
            ),
        ]
    )(image)


def _floating_model_dtype(net, fallback):
    """返回模型首个浮点参数的 dtype，用于让输入与权重精度保持一致。"""
    try:
        return next(param.dtype for param in net.parameters() if param.is_floating_point())
    except StopIteration:
        return fallback


def unload_model(reason: str = "") -> bool:
    """释放已加载模型，使下一次推理按照当前设置重新加载。"""
    global model, model_device, active_model_id
    old_model = model
    old_device = model_device
    if old_model is None:
        return False

    model = None
    model_device = None
    active_model_id = None
    del old_model
    gc.collect()
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    suffix = f"（{reason}）" if reason else ""
    push_user_log("info", f"已释放 {str(old_device or 'CPU').upper()} 模型{suffix}")
    return True


def _load_u2net(local_model_dir: str):
    """从本地 ModelScope 权重构造 U²-Net，不依赖运行时远程代码。"""
    import torch  # type: ignore
    from backend.services.u2net_model import U2NET

    weight_path = os.path.join(local_model_dir, "pytorch_model.pt")
    try:
        try:
            state = torch.load(weight_path, map_location="cpu", weights_only=True)
        except TypeError:  # 兼容旧版 PyTorch
            state = torch.load(weight_path, map_location="cpu")
        if isinstance(state, dict):
            for wrapper_key in ("state_dict", "model", "net"):
                if isinstance(state.get(wrapper_key), dict):
                    state = state[wrapper_key]
                    break
        if not isinstance(state, dict):
            raise TypeError("权重文件不是 PyTorch state_dict")
        if any(str(key).startswith("module.") for key in state):
            state = {str(key).removeprefix("module."): value for key, value in state.items()}
        net = U2NET(3, 1)
        net.load_state_dict(state, strict=True)
        return net
    except Exception as exc:
        raise AppException(
            UserError.MODEL_MISSING.value,
            "U²-Net 模型文件不完整或不匹配，请按下载提示重新放置全部文件。",
            inner=exc,
        ) from exc


def _load_ben2(local_model_dir: str):
    """加载 BEN2 官方模型代码及权重，始终只从统一本地缓存读取。"""
    import torch  # type: ignore

    source_path = os.path.join(local_model_dir, "BEN2.py")
    weight_path = os.path.join(local_model_dir, "BEN2_Base.pth")
    try:
        module_name = "removebg_ben2_runtime"
        module = sys.modules.get(module_name)
        if module is None:
            module_spec = importlib.util.spec_from_file_location(module_name, source_path)
            if module_spec is None or module_spec.loader is None:
                raise ImportError("无法载入 BEN2.py")
            module = importlib.util.module_from_spec(module_spec)
            sys.modules[module_name] = module
            module_spec.loader.exec_module(module)
        net = module.BEN_Base()
        try:
            net.loadcheckpoints(weight_path)
        except TypeError:
            state = torch.load(weight_path, map_location="cpu")
            if not isinstance(state, dict) or not isinstance(state.get("model_state_dict"), dict):
                raise TypeError("BEN2 权重不是预期的 model_state_dict")
            net.load_state_dict(state["model_state_dict"], strict=True)
        return net
    except Exception as exc:
        raise AppException(
            UserError.MODEL_MISSING.value,
            "BEN2 模型代码或权重不完整，请按下载提示重新放置 BEN2.py 和 BEN2_Base.pth。",
            inner=exc,
        ) from exc


def _load_inspyrenet(local_model_dir: str):
    """加载官方 transparent-background 内的 InSPyReNet Base 权重。

    不使用其 ``Remover`` 封装，避免它绕开应用下载提示自行写入用户目录。
    """
    import warnings
    import torch  # type: ignore

    # 上游包在导入时会顺带探测其未使用的 GUI 可选依赖 flet；本应用不提供
    # 该 GUI，因此静默这条无害提示，避免用户误认为抠图运行环境不完整。
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Failed to import flet.*")
        from transparent_background.InSPyReNet import InSPyReNet_SwinB  # type: ignore

    weight_path = os.path.join(local_model_dir, "ckpt_base.pth")
    try:
        net = InSPyReNet_SwinB(
            depth=64,
            pretrained=False,
            base_size=[1024, 1024],
            threshold=None,
        )
        try:
            state = torch.load(weight_path, map_location="cpu", weights_only=True)
        except TypeError:
            state = torch.load(weight_path, map_location="cpu")
        if not isinstance(state, dict):
            raise TypeError("InSPyReNet 权重不是 PyTorch state_dict")
        net.load_state_dict(state, strict=True)
        return net
    except Exception as exc:
        raise AppException(
            UserError.MODEL_MISSING.value,
            "InSPyReNet 权重或运行组件不完整，请重新运行 build_all.bat 后按下载提示获取 ckpt_base.pth。",
            inner=exc,
        ) from exc


def load_model(settings, model_id: str = DEFAULT_MODEL_ID) -> None:
    """将指定模型加载到当前实际设备；模型或设备变化时自动重新加载。"""
    global model, model_device, active_model_id
    model_id = str(model_id or DEFAULT_MODEL_ID).strip().lower()
    get_model_spec(model_id)  # 校验未知模型，不能静默回退
    device = resolve_device(settings)
    if model is not None and model_device == device and active_model_id == model_id:
        return
    if model is not None:
        unload_model(f"切换至 {model_id.upper()} / {device.upper()}")

    downloader = ModelDownloader(settings, model_id)
    if not downloader.is_model_ready():
        raise AppException(
            UserError.MODEL_MISSING.value,
            f"{downloader.model_label} 模型文件缺失，请先按下载提示放置模型。",
        )

    # 支持新 ModelScope 目录与用户此前手工放入的 Hugging Face 风格目录，
    # 并且始终把 from_pretrained 指向实际包含 config/权重/模型代码的同一目录。
    local_model_dir = downloader.model_dir

    try:
        import torch  # type: ignore
    except Exception as exc:
        raise AppException(
            UserError.INTERNAL.value,
            "运行环境缺少 torch，请先安装 backend/requirements.txt。",
            inner=exc,
        ) from exc

    try:
        if model_id == "u2net":
            net = _load_u2net(local_model_dir)
        elif model_id == "ben2":
            net = _load_ben2(local_model_dir)
        elif model_id == "inspyrenet":
            net = _load_inspyrenet(local_model_dir)
        else:
            from transformers import AutoModelForImageSegmentation  # type: ignore
            net = AutoModelForImageSegmentation.from_pretrained(
                local_model_dir,
                trust_remote_code=True,
                torch_dtype="auto",
            )
    except AppException:
        raise
    except Exception as exc:
        # RMBG-2.0 / BiRefNet 的远程模型代码会动态 import timm。这个错误不能被
        # “not found”误判为模型文件缺失，否则用户会反复下载已完整的权重。
        err_str = str(exc).lower()
        if (
            "requires the following packages" in err_str
            or "no module named 'timm'" in err_str
            or "no module named 'einops'" in err_str
            or "no module named 'cv2'" in err_str
            or "no module named 'transparent_background'" in err_str
        ):
            raise AppException(
                UserError.DEPENDENCY_ERROR.value,
                "模型运行依赖缺失，请重新运行 build_all.bat 重建环境。",
                inner=exc,
            ) from exc

        app_exc = from_exception(exc)
        # 仅在明确提及模型文件时标为 MODEL_MISSING；不能用通用的
        # “not found”或“config”，它们也会出现在动态依赖错误中。
        file_markers = (
            "no such file or directory",
            "model.safetensors",
            "pytorch_model.bin",
            "preprocessor_config.json",
            "config.json",
        )
        if any(marker in err_str for marker in file_markers):
            app_exc = AppException(
                UserError.MODEL_MISSING.value,
                f"{downloader.model_label} 模型文件缺失，请按下载提示重新放置全部文件。",
                inner=exc,
            )
        raise app_exc from exc

    try:
        net.to(device)
        # 官方 BiRefNet 标准权重为 FP16。CUDA 下保留半精度以节省显存；CPU
        # 运算则统一为 FP32，避免部分 CPU 算子不支持 Half 或输入精度不匹配。
        if model_id == "birefnet" and device == "cpu":
            net.float()
        net.eval()
    except Exception as exc:
        raise from_exception(exc) from exc

    # 记录为全局
    model = net
    model_device = device
    active_model_id = model_id

    # 设置 matmul 精度
    try:
        torch.set_float32_matmul_precision("high")
    except Exception:
        pass

    push_user_log("info", f"{downloader.model_label} 模型加载完成，推理设备：{device.upper()}")


# ---------------------------------------------------------------------------
# 参数化后处理工具
# ---------------------------------------------------------------------------
def _pil_resample(Image):
    """返回当前 Pillow 版本可用的 BILINEAR 重采样常量。"""
    try:
        return Image.Resampling.BILINEAR
    except AttributeError:
        return Image.BILINEAR


def _apply_threshold(mask_img, threshold: float):
    """对 PIL Image (灰度 L 模式) 按阈值做二值化；threshold<=0 保留原始概率。"""
    if threshold is None or threshold <= 0:
        return mask_img
    import numpy as np  # type: ignore

    arr = np.asarray(mask_img, dtype=np.float32) / 255.0
    # 硬阈值：>= 阈值保留，< 阈值置 0；不做最大压缩（保留 1.0 区域）
    arr = np.where(arr >= float(threshold), arr, 0.0)
    arr = (np.clip(arr, 0.0, 1.0) * 255.0).astype("uint8")
    from PIL import Image as _I  # type: ignore

    return _I.fromarray(arr, mode="L")


def _apply_feather(mask_img, radius: int):
    """对 Alpha 通道做高斯模糊（羽化），radius=0 跳过。"""
    if not radius or int(radius) <= 0:
        return mask_img
    radius = max(0, int(radius))
    try:
        # Pillow 自带高斯模糊
        from PIL import ImageFilter  # type: ignore
    except Exception:
        return mask_img
    # radius 过大时按图像短边限制，避免过度模糊
    short_side = min(mask_img.size)
    safe_r = min(radius, max(0, short_side // 20))
    if safe_r <= 0:
        return mask_img
    return mask_img.filter(ImageFilter.GaussianBlur(radius=safe_r))


def _apply_edge_refine(mask_img, strength: int, original_rgb):
    """
    基于原图的梯度/边缘信息，对 mask 的过渡区域做保留与增强。
    思路：对原图做灰度 → Sobel 边缘 → 和原 mask 的半透明区相乘，
    从而在边缘处（如发丝）保留更丰富的半透明过渡。
    strength: 0~4，0 关闭。
    """
    if not strength or int(strength) <= 0:
        return mask_img
    strength = max(0, min(4, int(strength)))
    try:
        import numpy as np  # type: ignore
        from PIL import Image, ImageFilter  # type: ignore
    except Exception:
        return mask_img

    # 1. 原图灰度
    gray = original_rgb.convert("L")
    # 2. 边缘检测
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edges_arr = np.asarray(edges, dtype=np.float32) / 255.0  # 0..1
    # 放大边缘影响
    edges_arr = np.clip(edges_arr * (0.4 + 0.3 * strength), 0.0, 1.0)

    mask_arr = np.asarray(mask_img, dtype=np.float32) / 255.0
    # 3. 在 mask 的 0.05~0.95 过渡区间内，叠加边缘信息
    transition = ((mask_arr > 0.05) & (mask_arr < 0.95)).astype(np.float32)
    refined = mask_arr + transition * edges_arr * (0.15 * strength)
    refined = np.clip(refined, 0.0, 1.0)

    refined_u8 = (refined * 255.0).astype("uint8")
    return Image.fromarray(refined_u8, mode="L")


def remove_background(
    input_image_path: str,
    output_dir: str,
    settings,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """对单张图片执行去背景，保存为 PNG（RGBA 透明）并返回元数据。

    :param params: 可选后处理参数
        - threshold: float, 应用层 Alpha 阈值（RMBG-2.0 的该用法同时被官方明确支持）
        - feather: int, 应用层 Alpha 羽化半径
        - edge_refine: int, 应用层边缘增强强度
        - alpha_matting_enabled: bool, 使用可选 PyMatting 半透明边缘精修
        - alpha_matting_foreground_threshold/background_threshold/erode_size: trimap 参数
        - ben2_refine_foreground: bool, 使用 BEN2 官方前景颜色重建与边缘精修
        - inspyrenet_dynamic_resize: bool, 使用 InSPyReNet 官方动态尺寸预处理
        - output_mode: str, 固定 'rgba'（透明 PNG）
    :returns: {'output_path': str, 'elapsed_sec': float, 'w': int, 'h': int}
    """
    global model

    # 默认参数兜底
    params = params or {}
    threshold = float(params.get("threshold", 0.5)) if params.get("threshold") is not None else 0.5
    threshold = max(0.0, min(1.0, threshold))
    feather = max(0, int(params.get("feather", 1)))
    edge_refine = max(0, min(4, int(params.get("edge_refine", 1))))
    alpha_matting_enabled = bool(params.get("alpha_matting_enabled", False))
    alpha_matting_foreground_threshold = max(1, min(255, int(params.get("alpha_matting_foreground_threshold", 240))))
    alpha_matting_background_threshold = max(0, min(254, int(params.get("alpha_matting_background_threshold", 10))))
    alpha_matting_erode_size = max(0, min(30, int(params.get("alpha_matting_erode_size", 10))))
    ben2_refine_foreground = str(params.get("ben2_refine_foreground", "false")).lower() in ("1", "true", "yes", "on")
    inspyrenet_dynamic_resize = str(params.get("inspyrenet_dynamic_resize", "true")).lower() in ("1", "true", "yes", "on")
    output_mode = str(params.get("output_mode", "rgba")).lower()
    if output_mode not in ("rgba", "whitebg"):
        output_mode = "rgba"
    model_id = str(params.get("model_id", DEFAULT_MODEL_ID) or DEFAULT_MODEL_ID).strip().lower()
    get_model_spec(model_id)  # 对 API 调用者同样严格校验
    input_size = _resolve_input_size(model_id)

    # 1) 确保模型已加载到当前实际设备（切换设备后会自动重新加载）
    load_model(settings, model_id)

    # 2) 打开图片并校验
    try:
        from PIL import Image  # type: ignore
    except Exception as exc:
        raise AppException(
            UserError.INTERNAL.value,
            "缺少 Pillow 依赖，请先安装 backend/requirements.txt。",
            inner=exc,
        ) from exc

    try:
        original = Image.open(input_image_path).convert("RGB")
    except AppException:
        raise
    except Exception as exc:
        app_exc = from_exception(exc)
        if app_exc.code != UserError.FILE_CORRUPTED.value:
            app_exc = AppException(
                UserError.FILE_CORRUPTED.value,
                "图片文件已损坏或无法读取，请更换其他图片",
                inner=exc,
            )
        raise app_exc from exc

    device = resolve_device(settings)

    try:
        import torch  # type: ignore
        from torchvision import transforms  # type: ignore
        from torchvision.transforms import ToPILImage  # type: ignore
    except Exception as exc:
        raise AppException(
            UserError.INTERNAL.value,
            "缺少 torch / torchvision 依赖，请先安装 backend/requirements.txt。",
            inner=exc,
        ) from exc

    resample_mode = _pil_resample(Image)

    t0 = time.time()
    try:
        transformed = None
        if model_id == "ben2":
            # BEN2 官方 inference 自己执行 1024 预处理，并可选前景颜色重建。
            pass
        elif model_id == "inspyrenet":
            transformed = build_inspyrenet_transform(
                original, inspyrenet_dynamic_resize
            ).unsqueeze(0).to(device)
        elif model_id in ("rmbg20", "birefnet"):
            transformed = build_transform(input_size)(original).unsqueeze(0).to(device)
        else:
            # U²-Net 的显著物体分割预处理沿用 ImageNet 归一化；输出会保留原图尺寸。
            transform = transforms.Compose([
                transforms.Resize((input_size, input_size)),
                transforms.ToTensor(),
                transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
            ])
            transformed = transform(original).unsqueeze(0).to(device)

        # BiRefNet 的官方 safetensors 通常为 FP16；TorchVision 预处理输出为
        # FP32。如果不在此处对齐，会在第一层卷积报 float / Half 不一致。
        if model_id == "birefnet" and transformed is not None:
            transformed = transformed.to(dtype=_floating_model_dtype(model, transformed.dtype))
    except Exception as exc:
        raise from_exception(exc) from exc

    try:
        result_rgb = original.copy()
        mask = None
        with torch.no_grad():
            if model_id == "ben2":
                ben2_result = model.inference(
                    original.copy(), refine_foreground=ben2_refine_foreground
                )
                if ben2_result.mode != "RGBA":
                    ben2_result = ben2_result.convert("RGBA")
                result_rgb = ben2_result.convert("RGB")
                mask = ben2_result.getchannel("A")
            else:
                raw = model(transformed)
            if model_id == "ben2":
                # BEN2 已直接给出 RGBA 结果与 Alpha 蒙版，无需再处理张量输出。
                pass
            elif model_id in ("rmbg20", "birefnet"):
                preds = raw[-1].sigmoid().cpu()
            elif model_id == "inspyrenet":
                # 官方 InSPyReNet 推理已 sigmoid 并按单图归一化，保留原始连续 alpha。
                preds = raw.float().cpu()
            else:
                # U²-Net 已在网络输出中 sigmoid，使用聚合输出 d0。
                preds = raw[0].float().cpu() if isinstance(raw, (tuple, list)) else raw.float().cpu()
                pred_min = preds.amin(dim=(-2, -1), keepdim=True)
                pred_max = preds.amax(dim=(-2, -1), keepdim=True)
                preds = (preds - pred_min) / (pred_max - pred_min).clamp_min(1e-8)
    except Exception as exc:
        raise from_exception(exc) from exc

    try:
        if mask is None:
            pred = preds[0].squeeze()
            mask = ToPILImage()(pred)
            mask = mask.resize(original.size, resample_mode)

        # Alpha Matting 必须先读取模型的原始连续 Alpha；若先做阈值会丢掉
        # trimap 所需的不确定区域。该组件随应用后端一同内置。
        if alpha_matting_enabled:
            try:
                mask = refine_alpha(
                    result_rgb,
                    mask,
                    foreground_threshold=alpha_matting_foreground_threshold,
                    background_threshold=alpha_matting_background_threshold,
                    erode_size=alpha_matting_erode_size,
                )
            except Exception as exc:
                raise AppException(
                    UserError.INTERNAL.value,
                    "Alpha Matting 精修失败，请关闭精修后重试，或调整其高级参数。",
                    inner=exc,
                ) from exc

        # 三项均为应用层实际后处理；其中 RMBG-2.0 官方还明确支持根据
        # 单通道 alpha 自定义前景阈值。它们不会改变模型的原生前向推理。
        mask = _apply_threshold(mask, threshold)
        if edge_refine > 0:
            mask = _apply_edge_refine(mask, edge_refine, result_rgb)
        mask = _apply_feather(mask, feather)

        # ---- 合成最终结果 ----
        result_rgb.putalpha(mask)  # -> RGBA

        os.makedirs(output_dir, exist_ok=True)
        basename = os.path.splitext(os.path.basename(input_image_path))[0]

        if output_mode == "whitebg":
            # 白底合成（RGB），输出 .jpg
            bg = Image.new("RGB", result_rgb.size, (255, 255, 255))
            bg.paste(result_rgb, mask=result_rgb.split()[-1])  # alpha 通道做蒙版
            out_path = os.path.join(output_dir, f"{basename}_{model_id}_noBG.jpg")
            bg.save(out_path, format="JPEG", quality=95)
        else:
            out_path = os.path.join(output_dir, f"{basename}_{model_id}_noBG.png")
            result_rgb.save(out_path, format="PNG")
    except AppException:
        raise
    except Exception as exc:
        raise from_exception(exc) from exc

    elapsed = time.time() - t0
    push_user_log("info", f"{get_model_spec(model_id)['label']} 抠图完成，耗时 {elapsed:.1f} 秒，已保存至 {out_path}")

    return {
        "output_path": out_path,
        "elapsed_sec": round(elapsed, 3),
        "w": int(original.width),
        "h": int(original.height),
        "params": {
            "output_mode": output_mode,
            "model_id": model_id,
            "input_size": input_size,
            "threshold": threshold,
            "feather": feather,
            "edge_refine": edge_refine,
            "alpha_matting_enabled": alpha_matting_enabled,
            "alpha_matting_foreground_threshold": alpha_matting_foreground_threshold,
            "alpha_matting_background_threshold": alpha_matting_background_threshold,
            "alpha_matting_erode_size": alpha_matting_erode_size,
            "ben2_refine_foreground": ben2_refine_foreground,
            "inspyrenet_dynamic_resize": inspyrenet_dynamic_resize,
        },
    }
