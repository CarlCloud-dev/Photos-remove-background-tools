# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for RemoveBG backend (onedir mode) — FAST mode.

Build command (run inside backend/ directory):
    pyinstaller --clean -y build_backend.spec
Output: backend/dist/backend/backend.exe

NOTE ON SPEED / SIZE:
  * Previously this spec used `collect_submodules('torch'/'torchvision'/
    'transformers'/'kornia'/'huggingface_hub')` which forces PyInstaller to
    analyse ~10 000 irrelevant submodules (e.g. transformers.models.whisper,
    kornia.models.sam3, torch.distributed.elastic, ...).  This caused 10+
    minute analysis runs and inflated the bundle size with unused code.
  * Today we list HIDDEN IMPORTS manually, matching exactly what
    `backend/app.py` and services import at runtime.  PyInstaller's own
    hooks (hook-torch.py / hook-transformers.py / hook-numpy.py / ...) still
    fire automatically and pull in the really required dynamic bits.
  * Model weights (*.safetensors / .bin) are NEVER packaged.  The app
    downloads them lazily on first run via download_service.py → the selected
    domestic-priority model source.  This is the user's explicitly requested
    behaviour.
"""

import os

from PyInstaller.utils.hooks import copy_metadata

block_cipher = None

HERE = os.path.dirname(os.path.abspath(SPEC)) if 'SPEC' in globals() else os.path.dirname(os.path.abspath(__file__))
ROOT = HERE  # spec lives in backend/
PROJECT_ROOT = os.path.dirname(ROOT)


def _datas():
    """Model companion files required by trust_remote_code=True loading.

    They exist both in the backend/ folder (for PyInstaller time) and will
    be re-downloaded by download_service.py into the HF cache folder at
    runtime.  We still ship them so from_pretrained() can import the local
    BiRefNet code even before the first download completes.
    """
    files = [
        'config.json',
        'preprocessor_config.json',
        'birefnet.py',
        'BiRefNet_config.py',
    ]
    result = []
    # pymatting reads its own installed version through importlib.metadata
    # during import. Hidden imports include its Python code but not its
    # ``*.dist-info`` directory, so explicitly ship that metadata too.
    result.extend(copy_metadata("pymatting"))

    for fname in files:
        src = os.path.join(ROOT, fname)
        if not os.path.isfile(src):
            # Skip missing files instead of crashing — download_service
            # will re-create them at runtime.
            continue
        result.append((src, '.'))
    return result


# ---------------------------------------------------------------------------
# Hidden imports — hand curated, ONLY what our code actually needs.
#   - Anything importable via static import in app.py / services/*.py is
#     picked up automatically and does NOT need to be listed here.
#   - We list dynamic / plugin-style imports that PyInstaller cannot see.
# ---------------------------------------------------------------------------
hiddenimports = [
    # ---- PyTorch / TorchVision core ----
    'torch',
    'torch.nn',
    'torch.nn.functional',
    'torch.utils',
    'torch.utils.data',
    'torchvision',
    'torchvision.transforms',
    'torchvision.transforms.functional',
    # RMBG preprocessing uses Resize / ToTensor.  These dynamic modules
    # are known to need explicit listing in some PyInstaller versions.
    'torchvision.io',
    'torchvision.io.image',

    # ---- Transformers (RMBG-2.0 / BiRefNet use AutoModelForImageSegmentation) ----
    'transformers',
    'transformers.models',
    'transformers.utils',
    'transformers.utils.hub',
    'transformers.utils.import_utils',
    # Auto* factories dynamically dispatch to class registered via factory
    # functions — make sure the RMBG-2.0 image-segmentation path works.
    'transformers.models.auto',
    'transformers.models.auto.image_processing_auto',
    'transformers.models.auto.modeling_auto',
    'transformers.models.auto.processing_auto',

    # ---- timm（RMBG-2.0 / BiRefNet 的 birefnet.py 动态导入） ----
    'timm',
    'timm.layers',
    'timm.models',
    'timm.models.layers',
    'timm.models.registry',

    # ---- BEN2 动态加载的官方模型代码 ----
    'einops',
    'einops.einops',
    'cv2',

    # ---- InSPyReNet（transparent-background 内置模型实现，直接本地加载权重） ----
    'transparent_background',
    'transparent_background.InSPyReNet',
    'transparent_background.modules.layers',
    'transparent_background.modules.context_module',
    'transparent_background.modules.attention_module',
    'transparent_background.modules.decoder_module',
    'transparent_background.backbones.SwinTransformer',

    # ---- safetensors（RMBG-2.0 / BiRefNet 权重均为 .safetensors） ----
    'safetensors',
    'safetensors.torch',

    # ---- Kornia (dependency of the BiRefNet backbone code path) ----
    'kornia',
    'kornia.color',
    'kornia.enhance',
    'kornia.filters',
    'kornia.geometry',
    'kornia.utils',

    # ---- 内置 Alpha Matting 精修（PyMatting + SciPy/Numba） ----
    'pymatting',
    'pymatting.alpha.estimate_alpha_cf',
    'pymatting.laplacian.cf_laplacian',
    'pymatting.solver.cg',
    'scipy',
    'scipy.ndimage',
    'scipy.sparse',
    'scipy.sparse.linalg',
    'numba',
    'numba.core',

    # ---- Serving / Web framework ----
    'waitress',
    'flask',
    'flask.json',
    'flask.logging',

    # ---- HuggingFace download path ----
    # NOTE: snapshot_download is a function exposed at package level (not a
    # submodule).  Listing 'huggingface_hub' alone is enough for PyInstaller
    # to pull in the whole package namespace.  Writing it as a submodule used
    # to cause 'Hidden import ... not found' on newer huggingface_hub versions.
    'huggingface_hub',

    # ---- Image codec support (Pillow plugins are often lazy-loaded) ----
    'PIL',
    'PIL.Image',
    'PIL.ImageFilter',
    'PIL.ImageOps',
    'PIL.PngImagePlugin',
    'PIL.JpegImagePlugin',
    'PIL.BmpImagePlugin',
    'PIL.WebPImagePlugin',
]


a = Analysis(
    [os.path.join(ROOT, 'app.py')],
    # app.py imports ``backend.*``.  The parent of backend/ must therefore be
    # searchable during Analysis; keeping ROOT too preserves top-level access
    # to the model companion files copied by ``_datas``.
    pathex=[PROJECT_ROOT, ROOT],
    binaries=[],
    datas=_datas(),
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Kornia decorates several functions with torch.jit.script at import time.
    # TorchScript uses inspect.getsourcelines(), so its package must retain
    # readable .py sources in addition to the normal PYZ archive.
    module_collection_mode={
        'kornia': 'pyz+py',
    },
    excludes=[
        # ---- Stuff we definitely don't ship; keeps PyInstaller fast ----
        # Dev / test frameworks
        'pytest',
        'doctest',
        'IPython',
        'notebook',
        'jupyter',
        'jupyter_core',
        'jupyter_client',
        # Do not exclude any ``torch.*`` modules. PyTorch imports parts of
        # distributed, quantization, FX, and unittest during normal top-level
        # initialization, even when this app only performs CPU inference.
        # TensorBoard itself remains optional and is not imported by torch.
        'tensorboard',
        # ML framework competitors (we use torch)
        'tensorflow',
        'jax',
        # ONNX runtime
        'onnx',
        'onnxruntime',
        # Training optimisers we never call
        'bitsandbytes',
        'accelerate',
        # xFormers (very large, already warned as unavailable)
        'xformers',
        # FLAX / JAX related transformers branches
        'transformers.models.*.modeling_flax*',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='backend',
    distpath=os.path.join(ROOT, 'dist'),
)
