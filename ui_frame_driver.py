"""Precise frame wakeups marshalled through Tk's thread-safe event queue.

Enable only while Tk.mainloop is running. Python 3.11+ uses a high-resolution
waitable timer for time.sleep on Windows; no spin loop or ctypes UI callbacks.
"""
import threading
import time
import tkinter as tk


class ThreadFrameDriver:
    EVENT = '<<Visual2CraftAnimationFrame>>'

    def __init__(self, root, callback, interval):
        self.root, self.callback, self.interval = root, callback, interval
        self.condition = threading.Condition()
        self.running = self.closing = False
        self.queued = False
        self.binding = root.bind(self.EVENT, self._dispatch, add='+')
        self.thread = threading.Thread(target=self._run, name='ui-frame-clock', daemon=True)
        self.thread.start()

    def _dispatch(self, _event=None):
        self.queued = False
        if self.running and not self.closing:
            self.callback()

    def start(self):
        with self.condition:
            self.running = True
            self.condition.notify()

    def stop(self):
        with self.condition:
            self.running = False

    def _run(self):
        while True:
            with self.condition:
                while not self.running and not self.closing:
                    self.condition.wait()
                if self.closing:
                    return
            deadline = time.monotonic()+self.interval
            while self.running and not self.closing:
                time.sleep(max(0, deadline-time.monotonic()))
                if self.running and not self.closing and not self.queued:
                    self.queued = True
                    try:
                        # _tkinter marshals this call to the UI thread. Actual
                        # widgets are touched only by the bound Tcl callback.
                        self.root.event_generate(self.EVENT, when='now')
                    except (tk.TclError, RuntimeError):
                        return  # The window/mainloop has already closed.
                deadline += self.interval
                now = time.monotonic()
                if deadline < now-self.interval:
                    deadline += int((now-deadline)/self.interval)*self.interval

    def close(self):
        with self.condition:
            if self.closing:
                return
            self.closing = True
            self.running = False
            self.condition.notify()
        # Never join here: a marshalled Tk call may be awaiting this UI thread.
        # The daemon exits after that call or the current <= 11.12 ms sleep.
        self.root.unbind(self.EVENT, self.binding)
