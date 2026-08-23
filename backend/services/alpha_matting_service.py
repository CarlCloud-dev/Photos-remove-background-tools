"""内置 Alpha Matting 精修。

模型先生成连续 Alpha，再以原图和三值 trimap 估算半透明边缘。依赖会随
项目环境和 PyInstaller 包一同安装，不在终端用户运行时联网下载。
"""

from __future__ import annotations

import numpy as np  # type: ignore
from PIL import Image  # type: ignore
from pymatting.alpha.estimate_alpha_cf import estimate_alpha_cf  # type: ignore
from scipy.ndimage import binary_erosion  # type: ignore


def refine_alpha(
    original_rgb,
    mask_img,
    foreground_threshold: int = 240,
    background_threshold: int = 10,
    erode_size: int = 10,
):
    """用原图和模型 Alpha 创建 trimap，并返回重新估计后的 Alpha。

    输入没有足够的前景、背景或未知区时原样返回，避免对纯色或全前景图片制造
    伪边缘。
    """

    foreground_threshold = max(1, min(255, int(foreground_threshold)))
    background_threshold = max(0, min(foreground_threshold - 1, int(background_threshold)))
    erode_size = max(0, min(30, int(erode_size)))

    alpha = np.asarray(mask_img.convert("L"), dtype=np.float64) / 255.0
    image = np.asarray(original_rgb.convert("RGB"), dtype=np.float64) / 255.0
    foreground = alpha >= (foreground_threshold / 255.0)
    background = alpha <= (background_threshold / 255.0)

    if erode_size:
        structure = np.ones((3, 3), dtype=bool)
        foreground = binary_erosion(foreground, structure=structure, iterations=erode_size)
        background = binary_erosion(background, structure=structure, iterations=erode_size)

    if not foreground.any() or not background.any():
        return mask_img

    trimap = np.full(alpha.shape, 0.5, dtype=np.float64)
    trimap[background] = 0.0
    trimap[foreground] = 1.0
    if not np.any(trimap == 0.5):
        return mask_img

    refined = estimate_alpha_cf(image, trimap)
    refined_u8 = (np.clip(refined, 0.0, 1.0) * 255.0).astype("uint8")
    return Image.fromarray(refined_u8, mode="L")
