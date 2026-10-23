"""One bounded, coalescing PIL worker. All Tk operations stay on the UI thread."""
from collections import OrderedDict
import queue
import sys
import threading
import time


class RasterWorker:
    def __init__(self, root):
        self.root = root
        self.condition = threading.Condition()
        self.jobs = OrderedDict()
        self.latest = {}
        self.results = queue.Queue()
        self.serial = 0
        self.closed = False
        self.after_id = None
        self.thread = threading.Thread(target=self._run, name='ui-raster', daemon=True)
        self.thread.start()

    @property
    def pending(self):
        return bool(self.latest)

    def submit(self, key, work, apply):
        with self.condition:
            if self.closed:
                return
            self.serial += 1
            token = self.serial
            self.latest[key] = token
            self.jobs[key] = (token, work, apply)
            self.condition.notify()
        if self.after_id is None:
            self.after_id = self.root.after(8, self._poll)

    def cancel_all(self):
        with self.condition:
            self.jobs.clear()
            self.latest.clear()

    def _run(self):
        while True:
            with self.condition:
                while not self.jobs and not self.closed:
                    self.condition.wait()
                if self.closed:
                    return
                key, (token, work, apply) = self.jobs.popitem(last=False)
            try:
                value, error = work(), None
            except Exception:
                value, error = None, sys.exc_info()
            if self.closed:
                return
            self.results.put((key, token, value, apply, error))

    def _poll(self):
        self.after_id = None
        if self.closed:
            return
        deadline = time.perf_counter()+.003
        while time.perf_counter() < deadline:
            try:
                key, token, value, apply, error = self.results.get_nowait()
            except queue.Empty:
                break
            if self.latest.get(key) != token:
                continue
            del self.latest[key]
            if error is not None:
                self.root.report_callback_exception(*error)
            else:
                try:
                    apply(value)
                except Exception:
                    self.root.report_callback_exception(*sys.exc_info())
        if self.latest or not self.results.empty():
            self.after_id = self.root.after(8, self._poll)

    def close(self):
        with self.condition:
            self.closed = True
            self.jobs.clear()
            self.latest.clear()
            self.condition.notify()
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        while not self.results.empty():
            try:
                self.results.get_nowait()
            except queue.Empty:
                break
