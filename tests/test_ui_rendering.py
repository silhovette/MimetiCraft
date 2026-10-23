"""Cache bounds, image isolation, stale-work rejection and UI-thread delivery."""
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image

from ui_rendering import PixelCache
from ui_widgets import RoundedButton, acrylic_pixels
from ui_worker import RasterWorker


class RenderingTests(unittest.TestCase):
    def test_pixel_cache_evicts_old_entries_and_never_exceeds_its_byte_budget(self):
        cache = PixelCache(budget=800)
        cache.get('a', lambda: Image.new('RGB', (10, 10)))
        cache.get('b', lambda: Image.new('RGB', (10, 10)))
        cache.get('a', lambda: self.fail('A cache hit must not rasterize again'))
        cache.get('c', lambda: Image.new('RGB', (10, 10)))
        self.assertEqual(set(cache.items), {'a', 'c'})
        cache.get('too-large', lambda: Image.new('RGB', (100, 100)))
        self.assertNotIn('too-large', cache.items)
        self.assertLessEqual(cache.bytes, cache.budget)

    def test_cached_masks_do_not_share_mutable_output_images(self):
        backdrop = Image.new('RGB', (310, 190), (60, 80, 100))
        params = (backdrop, '#101724', 17, '#FFFFFF', .48, 8, 2)
        first = acrylic_pixels(*params)
        expected = first.tobytes()
        first.paste('#FFFFFF', (0, 0, *first.size))
        self.assertEqual(acrylic_pixels(*params).tobytes(), expected)

    def pump(self, root, predicate):
        limit = time.monotonic()+2
        while not predicate() and time.monotonic() < limit:
            root.update()
            time.sleep(.002)
        self.assertTrue(predicate())

    def test_worker_coalesces_pending_work_and_applies_only_the_latest_on_ui_thread(self):
        root = tk.Tk()
        worker = RasterWorker(root)
        gate, started = threading.Event(), threading.Event()
        applied, middle = [], []
        ui_thread = threading.get_ident()
        def blocked():
            started.set()
            gate.wait(2)
            return 'obsolete'
        try:
            worker.submit('pane', blocked, lambda value: applied.append((value, threading.get_ident())))
            self.assertTrue(started.wait(1))
            worker.submit('pane', lambda: middle.append('should be superseded'), applied.append)
            worker.submit('pane', lambda: 'latest', lambda value: applied.append((value, threading.get_ident())))
            self.assertEqual(len(worker.jobs), 1)
            gate.set()
            self.pump(root, lambda: not worker.pending)
            self.assertEqual(applied, [('latest', ui_thread)])
            self.assertFalse(middle)
        finally:
            gate.set()
            worker.close()
            worker.thread.join(.5)
            self.assertFalse(worker.thread.is_alive())
            root.destroy()

    def test_resize_invalidation_rejects_a_result_that_finishes_late(self):
        root = tk.Tk()
        worker = RasterWorker(root)
        gate, started, finished = threading.Event(), threading.Event(), threading.Event()
        applied = []
        def blocked():
            started.set()
            gate.wait(2)
            finished.set()
            return 'old size'
        try:
            worker.submit('scene', blocked, applied.append)
            self.assertTrue(started.wait(1))
            worker.cancel_all()
            gate.set()
            self.pump(root, finished.is_set)
            root.update()
            self.assertFalse(applied)
        finally:
            gate.set()
            worker.close()
            worker.thread.join(.5)
            root.destroy()

    def test_unchanged_button_reuses_raster_but_hover_invalidates_it(self):
        root = tk.Tk()
        root.geometry('400x200')
        root._scene_pixels = Image.new('RGB', (400, 200), '#152030')
        button = RoundedButton(root, 'Example', lambda: None, font=('Segoe UI', -14))
        button.place(x=20, y=20, width=180, height=50)
        root.update()
        try:
            button._draw_chrome()
            first = button._photo
            button._draw_chrome()
            self.assertIs(button._photo, first)
            button._hover_amount = .5
            button._draw_chrome()
            self.assertIsNot(button._photo, first)
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
