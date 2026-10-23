"""Animation cadence, cancellation, mainloop dispatch and retained video images."""
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image

from ui_timing import AnimationClock, FRAME_SECONDS, FrameTimer, WindowTimerResolution


class FakeRoot:
    def __init__(self):
        self.timers = {}
        self.serial = 0
        self.errors = []

    def bind(self, *_a, **_kw):
        return 'binding'

    def after(self, milliseconds, callback):
        self.serial += 1
        token = f'after#{self.serial}'
        self.timers[token] = (milliseconds, callback)
        return token

    def after_cancel(self, token):
        self.timers.pop(token, None)

    def report_callback_exception(self, *_args):
        self.errors.append(_args)


class ClockTests(unittest.TestCase):
    def test_callback_work_does_not_accumulate_into_animation_period(self):
        root = FakeRoot()
        now = [100.]
        with patch('ui_timing.time.monotonic', side_effect=lambda: now[0]):
            clock = AnimationClock(root)
            first, second = [], []

            def animate_first():
                first.append(clock.frame_time)
                now[0] += .003  # 3 ms of rendering must not stretch each period.
                if len(first) < 10:
                    clock.schedule(animate_first)

            def animate_second():
                second.append(clock.frame_time)
                if len(second) < 10:
                    clock.schedule(animate_second)

            clock.schedule(animate_first)
            clock.schedule(animate_second)
            for _ in range(10):
                self.assertEqual(len(root.timers), 1)
                token, (delay, callback) = next(iter(root.timers.items()))
                root.timers.pop(token)
                now[0] += delay/1000
                callback()
            self.assertEqual(first, second)
            self.assertAlmostEqual(first[-1]-first[0], 9*FRAME_SECONDS)
            self.assertFalse(root.timers)
            self.assertFalse(root.errors)

    def test_cancel_during_dispatch_prevents_destroyed_widget_callback(self):
        root = FakeRoot()
        clock = AnimationClock(root)
        called = []
        clock.schedule(lambda: clock.cancel(other))
        other = clock.schedule(lambda: called.append('destroyed widget'))
        callback = next(iter(root.timers.values()))[1]
        root.timers.clear()
        callback()
        self.assertEqual(called, [])
        self.assertFalse(clock.pending)

    def test_ui_thread_dispatch_and_clean_shutdown_in_real_mainloop(self):
        root = tk.Tk()
        root.geometry('240x140')
        resolution = WindowTimerResolution(root)
        timer = FrameTimer(root)
        seen = []
        ui_thread = threading.get_ident()
        driver = None
        try:
            def animate():
                seen.append(threading.get_ident())
                timer.schedule(animate)
            timer.schedule(animate)
            timer.clock.enable_precise_driver()
            driver = timer.clock.driver
            root.after(250, root.quit)
            root.mainloop()
            self.assertGreater(len(seen), 3)
            self.assertEqual(set(seen), {ui_thread})
        finally:
            timer.clock.close()
            resolution.close()
            root.update()  # Finish any already marshalled event before joining.
            root.destroy()
            if driver is not None:
                driver.thread.join(timeout=.5)
                self.assertFalse(driver.thread.is_alive())
            self.assertFalse(resolution.active)

    def test_video_retains_a_complete_front_image_across_updates_and_resize(self):
        from video_surface import VideoSurface
        root = tk.Tk()
        canvas = tk.Canvas(root, width=120, height=100)
        canvas.pack()
        root.update()
        item = canvas.create_image(0, 0, anchor='nw')
        surface = VideoSurface(canvas, item)
        existing = set(root.tk.call('image', 'names'))
        try:
            names = None
            for index in range(60):
                color = (36+index, 78, 129)
                surface.present(Image.new('RGB', (101, 61), color))
                root.update()
                front = canvas.itemcget(item, 'image')
                pair = {str(photo) for photo in surface.photos}
                if names is None:
                    names = pair
                self.assertEqual(pair, names)
                self.assertIn(front, pair)
                self.assertEqual(tuple(root.tk.call(front, 'get', 50, 30)), color)
                self.assertEqual(set(root.tk.call('image', 'names'))-existing, pair)
            # A Configure alone keeps the last complete frame visible.
            previous = canvas.itemcget(item, 'image')
            canvas.configure(width=150, height=110)
            root.update()
            self.assertEqual(canvas.itemcget(item, 'image'), previous)
            surface.present(Image.new('RGB', (150, 110), (30, 40, 50)))
            root.update()
            front = canvas.itemcget(item, 'image')
            self.assertEqual(tuple(root.tk.call(front, 'get', 70, 50)), (30, 40, 50))
            self.assertEqual(len(set(root.tk.call('image', 'names'))-existing), 2)
            self.assertFalse(canvas.winfo_children(), 'No separate native video window')
            surface.close()
            self.assertEqual(canvas.itemcget(item, 'image'), '')
            self.assertEqual(set(root.tk.call('image', 'names')), existing)
        finally:
            surface.close()
            root.destroy()


if __name__ == '__main__':
    unittest.main()
