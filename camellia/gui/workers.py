"""Background workers for the Qt GUI."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from PySide6 import QtCore

from ..mc.proxy import MinecraftProxy, ProxyConfig


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
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            self._proxy = MinecraftProxy(self._config)
            loop.run_until_complete(self._proxy.start())
            self.started_proxy.emit(f"{self._config.listen_host}:{self._config.listen_port}")
            loop.run_forever()
        except Exception as exc:  # pylint: disable=broad-except
            self.error.emit(str(exc))
        finally:
            if self._loop and self._proxy:
                try:
                    self._loop.run_until_complete(self._proxy.stop())
                except Exception:  # pylint: disable=broad-except
                    pass
            if self._loop:
                self._loop.close()
            self.stopped_proxy.emit()

    def stop(self) -> None:
        if not self._loop:
            return
        if self._proxy:
            future = asyncio.run_coroutine_threadsafe(self._proxy.stop(), self._loop)
            future.add_done_callback(lambda _: self._loop.call_soon_threadsafe(self._loop.stop))
        else:
            self._loop.call_soon_threadsafe(self._loop.stop)
