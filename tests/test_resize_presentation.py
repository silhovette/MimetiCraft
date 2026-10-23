"""Coherent resize preview, stale commit rejection and native resource teardown."""
import ctypes
import os
import tkinter as tk
import unittest


@unittest.skipUnless(os.name == 'nt', 'Windows native resize presentation')
class ResizeTests(unittest.TestCase):
    def setUp(self):
        from ui_resize import ResizePresentation
        self.root = tk.Tk()
        self.root.geometry('360x240+60+60')
        self.root.configure(bg='#245681')
        canvas = tk.Canvas(self.root, bg='#FF8040', bd=0, highlightthickness=0)
        canvas.place(x=40, y=40, width=180, height=100)
        self.root.update()
        self.surface = ResizePresentation(self.root)
        self.surface.ready = True

    def tearDown(self):
        self.surface.close()
        self.root.destroy()

    def test_resize_scales_one_retained_complete_frame_including_native_children(self):
        surface = self.surface
        for width, height in ((400, 280), (440, 300), (480, 320)):
            self.root.geometry(f'{width}x{height}+60+60')
            self.root.update()
            self.assertTrue(surface.visible)
            self.assertEqual(surface.source_size, (360, 240))
            self.assertEqual(surface.target_size, (width, height))
            self.assertEqual(surface.captures, 1)
            self.assertEqual(surface.gdi.GetPixel(surface.dc, 60, 60), 0x4080FF)
            self.assertEqual(surface.gdi.GetPixel(surface.dc, 30, 130), 0x815624)
        self.assertGreater(surface.paints, 0)
        self.assertIsNone(surface.last_error)
        self.assertTrue(surface.commit(surface.version))
        self.assertFalse(surface.visible)
        self.assertIsNone(surface.bitmap)
        self.assertIsNone(surface.dc)

    def test_old_generation_and_active_drag_cannot_commit(self):
        surface = self.surface
        self.root.geometry('400x280+60+60')
        self.root.update()
        old_version = surface.version
        self.root.geometry('440x300+60+60')
        self.root.update()
        self.assertFalse(surface.commit(old_version))
        surface.active = True
        self.assertFalse(surface.commit(surface.version))
        surface.active = False
        self.assertTrue(surface.commit(surface.version))
        self.assertEqual(surface.commits, 1)

    def test_moving_without_resizing_does_not_capture_or_overlay(self):
        self.root.geometry('+90+90')
        self.root.update()
        self.assertFalse(self.surface.visible)
        self.assertEqual(self.surface.captures, 0)

    def test_close_removes_subclass_and_releases_bitmap_with_preview_visible(self):
        from ui_resize import _LIVE
        surface = self.surface
        self.root.geometry('400x280+60+60')
        self.root.update()
        self.assertIn(surface.hwnd, _LIVE)
        surface.close()
        self.assertNotIn(surface.hwnd, _LIVE)
        self.assertIsNone(surface.overlay)
        self.assertIsNone(surface.bitmap)
        self.assertIsNone(surface.dc)
        self.root.geometry('460x300+60+60')
        self.root.update()


if __name__ == '__main__':
    unittest.main()
