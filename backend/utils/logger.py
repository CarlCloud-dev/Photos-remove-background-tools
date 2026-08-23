"""统一日志模块：技术日志文件 + 控制台 + 用户前端日志队列（SSE）。"""

import datetime as _dt
import json
import logging
import os
import queue
import sys
import time
from logging.handlers import RotatingFileHandler
from typing import Generator, Literal, Optional

# ---------------------------------------------------------------------------
# 用户可见日志（前端 SSE 推送）
# ---------------------------------------------------------------------------

USER_LOG_QUEUE: "queue.Queue[dict]" = queue.Queue(maxsize=100)


def push_user_log(level: Literal["info", "warn", "error"], message: str) -> None:
    """将一条用户日志写入队列；队列满时丢弃最老的一条。

    :param level: 日志级别，字面量之一
    :param message: 日志内容（面向用户，不含技术细节）
    """
    safe_level = level if level in ("info", "warn", "error") else "info"
    item = {
        "level": safe_level,
        "message": str(message),
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    try:
        USER_LOG_QUEUE.put_nowait(item)
    except queue.Full:
        try:
            USER_LOG_QUEUE.get_nowait()
        except queue.Empty:
            pass
        try:
            USER_LOG_QUEUE.put_nowait(item)
        except queue.Full:  # pragma: no cover - race
            pass


def generate_user_logs() -> Generator[str, None, None]:
    """以 Server-Sent Events 格式持续产出用户日志。

    调用方应将该生成器传给 Flask ``Response``，mimetype 设为 ``text/event-stream``。
    """
    # 先立即发送一条心跳，让客户端确认连接已建立
    yield _sse_packet("info", "日志通道已连接")

    while True:
        try:
            item = USER_LOG_QUEUE.get(timeout=15.0)
        except queue.Empty:
            # 发送心跳保活
            yield _sse_packet("info", "", heartbeat=True)
            continue
        except Exception:
            # Generator 被客户端断开会抛出 GeneratorExit / 其他
            return

        try:
            data = json.dumps(item, ensure_ascii=False)
        except (TypeError, ValueError):
            data = json.dumps(
                {
                    "level": "error",
                    "message": "日志序列化失败",
                    "ts": _dt.datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
            )
        yield f"data: {data}\n\n"


def _sse_packet(
    level: Literal["info", "warn", "error"],
    message: str,
    heartbeat: bool = False,
) -> str:
    payload = {
        "level": level,
        "message": message if not heartbeat else "__heartbeat__",
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# 技术日志（文件 + 控制台）
# ---------------------------------------------------------------------------


def setup_logger(
    name: str = "app",
    log_dir: Optional[str] = None,
) -> logging.Logger:
    """创建/获取带文件轮转和控制台输出的 logger。

    :param name: logger 名称，重复调用同名 logger 会复用（避免重复 handler）
    :param log_dir: 日志目录；None 时不写文件，仅输出控制台
    :return: 配置好的 logger 实例
    """
    logger = logging.getLogger(name)
    # 避免重复添加 handler（多次调用 setup_logger）
    if getattr(logger, "_removebg_setup", False):
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s\n%(exc_info)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # exc_info 为空字符串时 formatter 会输出字面量 "%(exc_info)s"，因此我们自定义
    simple_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 1) 文件 handler: DEBUG + 含堆栈
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            date_tag = _dt.datetime.now().strftime("%Y%m%d")
            log_path = os.path.join(log_dir, f"app_{date_tag}.log")
            fh = RotatingFileHandler(
                log_path,
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(_StackFormatter())
            logger.addHandler(fh)
        except OSError:
            # 目录不可写时退回仅控制台
            pass

    # 2) 控制台 handler: INFO 级别
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(simple_fmt)
    logger.addHandler(sh)

    setattr(logger, "_removebg_setup", True)
    return logger


class _StackFormatter(logging.Formatter):
    """带完整堆栈（如存在）的日志格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        # 基础行
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        base = f"{ts} [{record.levelname}] {record.name}: {record.getMessage()}"
        # 异常堆栈
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            base = f"{base}\n{record.exc_text}"
        # stack_info
        if record.stack_info:
            base = f"{base}\n{self.formatStack(record.stack_info)}"
        return base
