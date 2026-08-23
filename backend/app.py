import argparse
import json
import logging
import os
import sys
import tempfile
import threading
from typing import Any, Dict, Optional

from flask import Flask, Response, jsonify, request, send_file

# ---------------------------------------------------------------------------
# sys.path：确保 `from backend.xxx` 可导入（项目根目录）
# ---------------------------------------------------------------------------
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if _BACKEND_DIR not in sys.path:
    # 兼容直接 `python app.py`（config.py 同目录）
    sys.path.insert(0, _BACKEND_DIR)

from backend.services.cuda_runtime_service import (  # noqa: E402
    activate_installed_cuda_runtime,
    cancel_cuda_runtime_download,
    runtime_info as get_cuda_runtime_info,
    start_cuda_runtime_download,
)

# Must run before importing model_service, which imports torch.  When the user
# has installed the optional sidecar, it takes precedence over the bundled CPU
# PyTorch on the next backend launch.
activate_installed_cuda_runtime()

from backend.config import (  # noqa: E402
    APP_ROOT,
    Settings,
    get_device_info,
    load_settings,
    resolve_device,
    save_settings,
)
from backend.services.download_service import (  # noqa: E402
    DEFAULT_MODEL_ID,
    MODEL_SPECS,
    ModelDownloader,
    cancel_model_download,
)
from backend.services.model_service import (  # noqa: E402
    load_model,
    remove_background,
    unload_model,
)
from backend.utils.errors import (  # noqa: E402
    AppException,
    UserError,
    from_exception,
)
from backend.utils.logger import (  # noqa: E402
    generate_user_logs,
    push_user_log,
    setup_logger,
)

__all__ = ["create_app", "main"]

VERSION = "1.0.0"

# 全局状态：运行时配置与模型是否已加载（仅用于 /api/status 暴露）
_state_lock = threading.Lock()
_runtime: Dict[str, Any] = {
    "settings": None,  # type: ignore[typeddict-item]
}


def get_settings() -> Settings:
    with _state_lock:
        s = _runtime.get("settings")
        if s is None:
            s = load_settings()
            _runtime["settings"] = s
        return s


def update_settings(new_values: Dict[str, Any]) -> Settings:
    with _state_lock:
        s = _runtime.get("settings")
        if s is None:
            s = load_settings()
        previous_device = s.DEVICE
        # 便携版只允许持久化推理设备；模型、日志、输出和插件目录始终跟随程序文件夹。
        aliases = {"device": "DEVICE"}
        for key, value in new_values.items():
            key = aliases.get(key, key)
            if key == "DEVICE":
                s.DEVICE = "cuda" if str(value or "").lower() == "cuda" else "cpu"
        save_settings(s)
        _runtime["settings"] = s
        s.ensure_dirs()
        if previous_device != s.DEVICE:
            unload_model("设置已更新，将在下次任务按新配置加载")
        return s


def _cors(response: Response) -> Response:
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization,X-Requested-With"
    return response


# ---------------------------------------------------------------------------
# 支持的图片扩展名 & 大小上限
# ---------------------------------------------------------------------------
_SUPPORTED_EXTS: tuple = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB


def _validate_ext(filename: str) -> None:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in _SUPPORTED_EXTS:
        raise AppException(
            UserError.UNSUPPORTED_FORMAT.value,
            "不支持的图片格式，仅支持 jpg / jpeg / png / bmp / webp",
        )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app() -> Flask:
    settings = get_settings()
    settings.ensure_dirs()

    # 技术日志（文件 + 控制台）
    logger = setup_logger(name="app", log_dir=settings.LOG_DIR)
    logger.info("RemoveBG backend starting, APP_ROOT=%s", APP_ROOT)

    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    app.config["MAX_CONTENT_LENGTH"] = 128 * 1024 * 1024  # 外层保护，内部有 50MB 细粒度
    app.after_request(_cors)

    # ------------------------------------------------------------------
    # 全局异常处理：识别 AppException，内部错误仅返回通用消息，写日志
    # ------------------------------------------------------------------
    @app.errorhandler(Exception)
    def _global_exception_handler(exc: Exception):  # noqa: ARG001
        if isinstance(exc, AppException):
            logger.warning(
                "AppException code=%s msg=%s inner=%s",
                exc.code,
                exc.user_message,
                exc.inner,
                exc_info=exc.inner is not None,
            )
            status = 400 if exc.code != UserError.INTERNAL.value else 500
            return (
                jsonify({"code": exc.code, "message": exc.user_message}),
                status,
            )

        # 非 AppException -> 走智能映射，仍无法识别默认 9999
        app_exc = from_exception(exc)
        logger.exception(
            "Unhandled exception mapped to code=%s: %s", app_exc.code, exc
        )
        return (
            jsonify({"code": app_exc.code, "message": app_exc.user_message}),
            500,
        )

    # ------------------------------------------------------------------
    # 健康 / 状态 / 配置
    # ------------------------------------------------------------------
    @app.route("/health", methods=["GET"])
    def health():
        settings = get_settings()
        device_info = get_device_info(settings)
        return jsonify(
            {
                "status": "ok",
                "version": VERSION,
                "device": device_info["actual_device"],
                "device_info": device_info,
            }
        )

    @app.route("/api/status", methods=["GET"])
    def api_status():
        settings = get_settings()
        model_id = request.args.get("model_id", DEFAULT_MODEL_ID)
        try:
            downloader = ModelDownloader(settings, model_id)
        except ValueError as exc:
            return jsonify({"code": 4000, "message": str(exc)}), 400
        import backend.services.model_service as _ms

        model_ready = _ms.model is not None and _ms.active_model_id == downloader.model_id
        device_info = get_device_info(settings)
        return jsonify(
            {
                "model_id": downloader.model_id,
                "model_loaded": bool(model_ready),
                "model_cached": downloader.is_model_ready(),
                "device": device_info["actual_device"],
                "device_info": device_info,
                "model_device": _ms.model_device,
                "model_cache_dir": downloader.local_dir,
                "model_download": downloader.manual_download_info(),
                "available_models": [
                    {"id": key, "label": str(spec["label"])}
                    for key, spec in MODEL_SPECS.items()
                ],
            }
        )

    @app.route("/api/config", methods=["GET", "POST"])
    def api_config():
        if request.method == "GET":
            current = get_settings()
            return jsonify(
                {
                    "code": 0,
                    "message": "ok",
                    "settings": {
                        "APP_PORT": current.APP_PORT,
                        "APP_HOST": current.APP_HOST,
                        "MODEL_NAME": current.MODEL_NAME,
                        "MODEL_CACHE_DIR": current.MODEL_CACHE_DIR,
                        "DEVICE": current.DEVICE,
                        "LOG_DIR": current.LOG_DIR,
                        "OUTPUT_DIR": current.OUTPUT_DIR,
                        "PLUGIN_DIR": current.PLUGIN_DIR,
                    },
                    "device_info": get_device_info(current),
                }
            )
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"code": 4000, "message": "请求体必须是 JSON 对象"}), 400
        new_settings = update_settings(payload)
        return jsonify(
            {
                "code": 0,
                "message": "ok",
                "settings": {
                    "APP_PORT": new_settings.APP_PORT,
                    "APP_HOST": new_settings.APP_HOST,
                    "MODEL_NAME": new_settings.MODEL_NAME,
                    "MODEL_CACHE_DIR": new_settings.MODEL_CACHE_DIR,
                    "DEVICE": new_settings.DEVICE,
                        "LOG_DIR": new_settings.LOG_DIR,
                        "OUTPUT_DIR": new_settings.OUTPUT_DIR,
                        "PLUGIN_DIR": new_settings.PLUGIN_DIR,
                },
                "device_info": get_device_info(new_settings),
            }
        )

    # ------------------------------------------------------------------
    # 去背景接口：multipart 上传 或 JSON input_path
    # ------------------------------------------------------------------
    @app.route("/api/removebg", methods=["POST"])
    def api_removebg():
        settings = get_settings()
        settings.ensure_dirs()
        output_dir = os.path.abspath(settings.OUTPUT_DIR)

        input_path: Optional[str] = None
        temp_fd = None
        temp_path: Optional[str] = None
        original_filename: str = ""

        # 1) 解析请求：优先 multipart 的 image 字段，其次 JSON body.input_path
        if request.content_type and "multipart/form-data" in request.content_type:
            file = request.files.get("image")
            if file is None:
                raise AppException(
                    UserError.UNSUPPORTED_FORMAT.value,
                    "multipart 上传必须使用字段名 'image'",
                )
            original_filename = file.filename or ""
            _validate_ext(original_filename)

            # 大小校验（Flask MAX_CONTENT_LENGTH 已做外层；此处用 stream 再精细判断）
            data = file.read()
            if len(data) > _MAX_UPLOAD_BYTES:
                raise AppException(
                    UserError.UPLOAD_TOO_LARGE.value,
                    "上传的图片过大（最大 50MB）",
                )

            ext = os.path.splitext(original_filename)[1].lower() or ".png"
            temp_fd, temp_path = tempfile.mkstemp(prefix="rmbg_in_", suffix=ext)
            with os.fdopen(temp_fd, "wb") as f:
                f.write(data)
            temp_fd = None
            input_path = temp_path
        else:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                raise AppException(
                    4000,
                    "请求体必须是 JSON 对象 {'input_path': '...'}，或使用 multipart 上传 image 字段",
                )
            raw_input = payload.get("input_path")
            if not isinstance(raw_input, str) or not raw_input:
                raise AppException(
                    4000,
                    "缺少必填字段 'input_path'（本地图片绝对路径）",
                )
            _validate_ext(raw_input)

            # 大小校验
            try:
                sz = os.path.getsize(raw_input)
            except OSError as exc:
                raise AppException(
                    UserError.FILE_CORRUPTED.value,
                    "图片文件已损坏或无法读取，请更换其他图片",
                    inner=exc,
                ) from exc
            if sz > _MAX_UPLOAD_BYTES:
                raise AppException(
                    UserError.UPLOAD_TOO_LARGE.value,
                    "上传的图片过大（最大 50MB）",
                )
            input_path = os.path.abspath(raw_input)
            original_filename = os.path.basename(input_path)

        # 2) 调模型服务执行抠图
        #    读取 multipart 参数（应用后处理 + 输出模式），与 JSON 体兜底
        params: Dict[str, Any] = {}
        try:
            raw_t = request.form.get("threshold")
            raw_f = request.form.get("feather")
            raw_e = request.form.get("edge_refine")
            raw_alpha_matting_enabled = request.form.get("alpha_matting_enabled")
            raw_alpha_matting_fg = request.form.get("alpha_matting_foreground_threshold")
            raw_alpha_matting_bg = request.form.get("alpha_matting_background_threshold")
            raw_alpha_matting_erode = request.form.get("alpha_matting_erode_size")
            raw_ben2_refine_foreground = request.form.get("ben2_refine_foreground")
            raw_inspyrenet_dynamic_resize = request.form.get("inspyrenet_dynamic_resize")
            raw_m = request.form.get("output_mode")
            raw_model_id = request.form.get("model_id")
            if raw_t is not None and raw_t != "":
                try: params["threshold"] = float(raw_t)
                except (TypeError, ValueError): pass
            if raw_f is not None and raw_f != "":
                try: params["feather"] = int(raw_f)
                except (TypeError, ValueError): pass
            if raw_e is not None and raw_e != "":
                try: params["edge_refine"] = int(raw_e)
                except (TypeError, ValueError): pass
            if raw_alpha_matting_enabled is not None:
                params["alpha_matting_enabled"] = str(raw_alpha_matting_enabled).lower() in ("1", "true", "yes", "on")
            if raw_alpha_matting_fg is not None and raw_alpha_matting_fg != "":
                try: params["alpha_matting_foreground_threshold"] = int(raw_alpha_matting_fg)
                except (TypeError, ValueError): pass
            if raw_alpha_matting_bg is not None and raw_alpha_matting_bg != "":
                try: params["alpha_matting_background_threshold"] = int(raw_alpha_matting_bg)
                except (TypeError, ValueError): pass
            if raw_alpha_matting_erode is not None and raw_alpha_matting_erode != "":
                try: params["alpha_matting_erode_size"] = int(raw_alpha_matting_erode)
                except (TypeError, ValueError): pass
            if raw_ben2_refine_foreground is not None:
                params["ben2_refine_foreground"] = str(raw_ben2_refine_foreground).lower() in ("1", "true", "yes", "on")
            if raw_inspyrenet_dynamic_resize is not None:
                params["inspyrenet_dynamic_resize"] = str(raw_inspyrenet_dynamic_resize).lower() in ("1", "true", "yes", "on")
            if raw_m:
                params["output_mode"] = str(raw_m)
            if raw_model_id:
                params["model_id"] = str(raw_model_id)
        except Exception:
            params = {}
        # JSON body 参数兜底（仅 multipart 不存在时才取）
        if not params and not request.content_type or (
            request.content_type and "multipart/form-data" not in request.content_type
        ):
            # 已在上方 payload 分支处理
            pass

        try:
            result = remove_background(input_path, output_dir, settings, params=params or None)
        finally:
            # 清理临时文件
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

        # 3) 返回统一响应
        return jsonify({"code": 0, "message": "ok", "data": result})

    # ------------------------------------------------------------------
    # 清理应用输出图片：只作用于当前应用根目录下的 output，不递归删除目录。
    # ------------------------------------------------------------------
    @app.route("/api/output/clear", methods=["POST"])
    def api_clear_output_images():
        settings = get_settings()
        settings.ensure_dirs()
        output_root = os.path.abspath(settings.OUTPUT_DIR)
        expected_output_root = os.path.abspath(os.path.join(APP_ROOT, "output"))
        if os.path.normcase(output_root) != os.path.normcase(expected_output_root):
            return jsonify({"code": 4003, "message": "仅允许清理应用内 output 目录，已拒绝当前路径。"}), 400

        deleted_count = 0
        failed_count = 0
        try:
            with os.scandir(output_root) as entries:
                for entry in entries:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    if os.path.splitext(entry.name)[1].lower() not in _SUPPORTED_EXTS:
                        continue
                    try:
                        os.remove(entry.path)
                        deleted_count += 1
                    except OSError:
                        failed_count += 1
        except OSError as exc:
            logging.getLogger("app").exception("清理输出图片失败，目录=%s", output_root)
            return jsonify({"code": 9999, "message": f"清理图片缓存失败：{exc}"}), 500

        push_user_log("info", f"已清除 output 图片缓存：{deleted_count} 张")
        message = f"已清除 {deleted_count} 张图片缓存。"
        if failed_count:
            message += f"另有 {failed_count} 张文件未能删除。"
        return jsonify({
            "code": 0,
            "message": message,
            "deleted_count": deleted_count,
            "failed_count": failed_count,
        })

    # ------------------------------------------------------------------
    # 预览接口：返回 output 目录下的图片（防路径穿越，允许绝对/相对路径）
    # ------------------------------------------------------------------
    @app.route("/api/preview", methods=["GET"])
    def api_preview():
        settings = get_settings()
        output_root = os.path.abspath(settings.OUTPUT_DIR)
        rel = request.args.get("path", "") or ""
        if not rel:
            return jsonify({"code": 4000, "message": "缺少 query 参数 path"}), 400
        ext = os.path.splitext(rel)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg"):
            return jsonify({"code": UserError.UNSUPPORTED_FORMAT.value, "message": "仅允许预览 png / jpg 文件"}), 400
        # 绝对路径且直接位于 output_root 下 -> 直接接受
        if os.path.isabs(rel):
            candidate = os.path.abspath(rel)
        else:
            candidate = os.path.abspath(os.path.join(output_root, rel))
        if not (candidate == output_root or (candidate + os.sep).startswith(output_root + os.sep)):
            return jsonify({"code": 4003, "message": "非法的 path 参数"}), 400
        if not os.path.isfile(candidate):
            return jsonify({"code": 404, "message": "文件不存在"}), 404
        ext = os.path.splitext(candidate)[1].lower()
        if ext in (".jpg", ".jpeg"):
            mimetype = "image/jpeg"
        else:
            mimetype = "image/png"
        return send_file(candidate, mimetype=mimetype)

    # ------------------------------------------------------------------
    # 下载进度 SSE
    # ------------------------------------------------------------------
    @app.route("/api/download/events", methods=["GET"])
    def api_download_events():
        settings = get_settings()
        model_id = request.args.get("model_id", DEFAULT_MODEL_ID)
        try:
            downloader = ModelDownloader(settings, model_id)
        except ValueError as exc:
            payload = {"event": "error", "message": str(exc)}
            return Response(
                response=iter([f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"]),
                mimetype="text/event-stream",
            )

        # 下载必须由用户在前端提示框中明确确认，不能因一次任务请求而静默开始。
        confirmed = request.args.get("confirm", "")
        if confirmed not in ("1", "true", "True", "yes"):
            payload = {
                "event": "error",
                "message": "请先在下载提示中确认自动下载，或手动下载模型后重新检测。",
            }
            return Response(
                response=iter([f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"]),
                mimetype="text/event-stream",
            )

        if downloader.is_model_ready():
            payload = {
                "event": "done",
                "percent": 100,
                "local_dir": downloader.local_dir,
            }
            return Response(
                response=iter([f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"]),
                mimetype="text/event-stream",
            )

        # 否则启动下载并流式返回
        mirror_param = request.args.get("mirror", "1")
        mirror = mirror_param not in ("0", "false", "False", "no")
        gen = downloader.start_download(mirror=mirror)
        return Response(response=gen, mimetype="text/event-stream")

    @app.route("/api/download/cancel", methods=["POST"])
    def api_cancel_download():
        """取消当前模型下载，并清理由该下载产生的未完成文件。"""
        settings = get_settings()
        body = request.get_json(silent=True) or {}
        model_id = str(body.get("model_id") or request.form.get("model_id") or DEFAULT_MODEL_ID)
        try:
            result = cancel_model_download(settings, model_id)
        except ValueError as exc:
            return jsonify({"code": 4001, "message": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001 - cancellation must surface a usable message
            logging.getLogger("app").exception("取消模型下载失败，model=%s", model_id)
            return jsonify({"code": 9999, "message": f"取消下载失败：{exc}"}), 500

        if result["active"] and not result["completed"]:
            message = "正在停止下载，文件连接释放后会自动清理未完成文件。"
        elif result["active"]:
            message = "下载已取消，未完成文件已清理。"
        else:
            message = "未发现进行中的下载，已清理遗留的未完成文件。"
        return jsonify({"code": 0, "message": message, **result})

    # ------------------------------------------------------------------
    # Optional CUDA runtime (kept out of the base installer)
    # ------------------------------------------------------------------
    @app.route("/api/runtime/cuda/status", methods=["GET"])
    def api_cuda_runtime_status():
        return jsonify({"code": 0, **get_cuda_runtime_info()})

    @app.route("/api/runtime/cuda/events", methods=["GET"])
    def api_cuda_runtime_events():
        confirmed = request.args.get("confirm", "")
        if confirmed not in ("1", "true", "True", "yes"):
            payload = {"event": "error", "message": "请先在 CUDA 提示中确认下载。"}
            return Response(response=iter([f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"]), mimetype="text/event-stream")
        return Response(response=start_cuda_runtime_download(), mimetype="text/event-stream")

    @app.route("/api/runtime/cuda/cancel", methods=["POST"])
    def api_cuda_runtime_cancel():
        return jsonify({"code": 0, **cancel_cuda_runtime_download()})

    # ------------------------------------------------------------------
    # 用户日志 SSE
    # ------------------------------------------------------------------
    @app.route("/api/logs/events", methods=["GET"])
    def api_logs_events():
        return Response(response=generate_user_logs(), mimetype="text/event-stream")

    return app


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RemoveBG backend server (RMBG-2.0)")
    parser.add_argument("--host", type=str, default=None, help="绑定地址, 默认读取 config.APP_HOST")
    parser.add_argument("--port", type=int, default=None, help="监听端口, 默认读取 config.APP_PORT")
    parser.add_argument("--dev", action="store_true", help="使用 Flask 开发模式(不使用 waitress)")
    parser.add_argument("--no-reload", action="store_true", help="禁用 Flask 自带重载（由桌面开发器负责重启）")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = _parse_args(argv)
    app = create_app()
    settings = get_settings()

    host = args.host or settings.APP_HOST or "127.0.0.1"
    port = int(args.port or settings.APP_PORT or 49173)

    frozen = getattr(sys, "frozen", False)
    use_dev_server = bool(args.dev) or (not frozen and not args.__dict__.get("waitress_only", False))

    logger = logging.getLogger("app")
    logger.info("Listening on %s:%s, mode=%s, frozen=%s", host, port, "dev" if use_dev_server else "waitress", frozen)

    if use_dev_server:
        app.run(host=host, port=port, debug=False, use_reloader=bool(not frozen and not args.no_reload))
    else:
        from waitress import serve

        serve(app, host=host, port=port, threads=8, ident="RemoveBG-Backend")


if __name__ == "__main__":
    main()
