"""Deadline-based 90 Hz Tk animation timing without busy waiting."""
import ctypes
import math
import os
import sys
import time

UI_FPS = 90
FRAME_SECONDS = 1 / UI_FPS


class AnimationClock:
    """One Tk timer for all animations, followed by one Tk repaint opportunity."""
    def __init__(self, root):
        self.root = root
        self.pending = {}
        self.serial = 0
        self.after_id = None
        self.deadline = None
        self.frame_time = None
        self.dispatching = False
        self.armed = False
        self.driver = None
        root.bind('<Destroy>', self._destroyed, add='+')

    def enable_precise_driver(self):
        """Switch once, immediately before entering Tk's real mainloop."""
        if self.driver is not None:
            return
        from ui_frame_driver import ThreadFrameDriver
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        self.driver = ThreadFrameDriver(self.root, self._tick, FRAME_SECONDS)
        if self.pending:
            self.deadline = time.monotonic()+FRAME_SECONDS
            self._arm()

    def schedule(self, callback):
        self.serial += 1
        token = f'frame#{self.serial}'
        self.pending[token] = callback
        if not self.armed and not self.dispatching:
            self.deadline = time.monotonic()+FRAME_SECONDS
            self._arm()
        return token

    def _arm(self):
        self.armed = True
        if self.driver is not None:
            self.driver.start()
            return
        delay = max(1, math.ceil((self.deadline-time.monotonic())*1000))
        self.after_id = self.root.after(delay, self._tick)

    def _tick(self):
        self.after_id = None
        now = time.monotonic()
        skipped = max(0, math.floor((now-self.deadline)/FRAME_SECONDS)-1)
        self.deadline += skipped*FRAME_SECONDS
        self.frame_time = self.deadline
        self.dispatching = True
        try:
            # New subscriptions are for the NEXT tick. Cancellation by another
            # callback still takes effect during this tick.
            for token in tuple(self.pending):
                callback = self.pending.pop(token, None)
                if callback is not None:
                    try:
                        callback()
                    except Exception:
                        self.root.report_callback_exception(*sys.exc_info())
        finally:
            self.dispatching = False
            self.frame_time = None
        if self.pending:
            self.deadline += FRAME_SECONDS
            self._arm()
        else:
            self.armed = False
            if self.driver is not None:
                self.driver.stop()

    def cancel(self, token):
        self.pending.pop(token, None)
        if not self.pending:
            self.armed = False
            if self.after_id is not None:
                self.root.after_cancel(self.after_id)
                self.after_id = None
            if self.driver is not None:
                self.driver.stop()

    def _destroyed(self, event):
        if event.widget is self.root:
            self.close()

    def close(self):
        self.pending.clear()
        self.armed = False
        if self.driver is not None:
            self.driver.close()
            self.driver = None
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None


class FrameTimer:
    """Widget handle for the window's shared 90 Hz animation clock."""
    def __init__(self, widget):
        self.widget = widget
        root = widget.winfo_toplevel()
        if not hasattr(root, '_animation_clock'):
            root._animation_clock = AnimationClock(root)
        self.clock = root._animation_clock

    @property
    def now(self):
        return self.clock.frame_time if self.clock.frame_time is not None else time.monotonic()

    def schedule(self, callback):
        return self.clock.schedule(callback)

    def cancel(self, token):
        if str(token).startswith('frame#'):
            self.clock.cancel(token)
        else:
            self.widget.after_cancel(token)


class WindowTimerResolution:
    """Pair the Windows 1 ms request with release on unmap and destruction."""
    def __init__(self, window):
        self.active = False
        self.api = None
        if os.name == 'nt':
            try:
                self.api = ctypes.WinDLL('winmm')
            except OSError:
                pass
        window.bind('<Map>', self._map, add='+')
        window.bind('<Unmap>', self._unmap, add='+')
        window.bind('<Destroy>', self._unmap, add='+')
        self.window = window

    def _map(self, event):
        if event.widget is self.window and self.api and not self.active:
            self.active = self.api.timeBeginPeriod(1) == 0

    def _unmap(self, event):
        if event.widget is self.window:
            self.close()

    def close(self):
        if self.active:
            self.api.timeEndPeriod(1)
            self.active = False
