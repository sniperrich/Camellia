"""Background workers for the Qt GUI."""

from __future__ import annotations

import asyncio
import logging
import os
import traceback
from datetime import datetime
from typing import Any, Callable

from PySide6 import QtCore

from ..mc.proxy import MinecraftProxy, ProxyConfig

_LOGGER = logging.getLogger("camellia.proxy.thread")

class WorkerSignals(QtCore.QObject):
    finished = QtCore.Signal(object)
    error = QtCore.Signal(str)


class Worker(QtCore.QRunnable):
    def __init__(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @QtCore.Slot()
    def run(self) -> None:
        try:
            result = self.func(*self.args, **self.kwargs)
        except Exception as exc:  # pylint: disable=broad-except
            message = str(exc) or "发生未知错误"
            self.signals.error.emit(message)
        else:
            self.signals.finished.emit(result)


class ProxyThread(QtCore.QThread):
    started_proxy = QtCore.Signal(str)
    stopped_proxy = QtCore.Signal()
    error = QtCore.Signal(str)

    def __init__(self, config: ProxyConfig, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._proxy: MinecraftProxy | None = None

    def run(self) -> None:
        try:
            if os.name == "nt":
                # Avoid Proactor quirks in packaged builds by using selector loop policy.
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            _LOGGER.info(
                "ProxyThread starting on %s:%s -> %s:%s",
                self._config.listen_host,
                self._config.listen_port,
                self._config.forward_host,
                self._config.forward_port,
            )
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            self._proxy = MinecraftProxy(self._config)
            loop.run_until_complete(self._proxy.start())
            try:
                self.started_proxy.emit(f"{self._config.listen_host}:{self._config.listen_port}")
            except Exception as exc:  # pylint: disable=broad-except
                _LOGGER.warning("ProxyThread started_proxy emit failed: %s", exc)
            loop.run_forever()
        except Exception as exc:  # pylint: disable=broad-except
            message = str(exc)
            try:
                base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, "logs"))
                os.makedirs(base_dir, exist_ok=True)
                path = os.path.join(base_dir, "proxy_thread_error.log")
                with open(path, "a", encoding="utf-8") as handle:
                    handle.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")
                    handle.write(traceback.format_exc())
                    handle.write("\n")
            except OSError:
                pass
            self.error.emit(message)
        finally:
            if self._loop and self._proxy:
                try:
                    self._loop.run_until_complete(self._proxy.stop())
                except Exception:  # pylint: disable=broad-except
                    pass
            if self._loop:
                self._loop.close()
            try:
                self.stopped_proxy.emit()
            except Exception as exc:  # pylint: disable=broad-except
                _LOGGER.warning("ProxyThread stopped_proxy emit failed: %s", exc)

    def stop(self) -> None:
        _LOGGER.info("ProxyThread stop requested")
        if not self._loop:
            return
        if self._proxy:
            future = asyncio.run_coroutine_threadsafe(self._proxy.stop(), self._loop)
            future.add_done_callback(lambda _: self._loop.call_soon_threadsafe(self._loop.stop))
        else:
            self._loop.call_soon_threadsafe(self._loop.stop)
