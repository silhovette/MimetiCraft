"""Windows resize presentation: one retained complete frame, then one commit.

Native callbacks only use Win32/GDI and plain Python state. They never call Tk:
calling Tk directly from a ctypes window procedure breaks _tkinter thread state.
The application polls the state on its normal UI clock and renders behind this
temporary surface. Snapshot pixels stay in memory and are never written to disk.
"""
import ctypes
from ctypes import wintypes as wt
import os
import time
from collections import deque

_LIVE = {}  # Retain callbacks until their native subclass has been removed.


class ResizePresentation:
    def __init__(self, root):
        if os.name != 'nt':
            raise OSError('Native resize presentation is only available on Windows')
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.gdi = ctypes.WinDLL('gdi32', use_last_error=True)
        self.comctl = ctypes.WinDLL('comctl32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.wnd_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t)
        self.sub_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, ctypes.c_size_t,
                                          ctypes.c_ssize_t, ctypes.c_size_t, ctypes.c_size_t)
        class WindowClass(ctypes.Structure):
            _fields_ = [('style', wt.UINT), ('proc', self.wnd_type), ('extra_class', ctypes.c_int),
                        ('extra_window', ctypes.c_int), ('instance', wt.HINSTANCE), ('icon', wt.HICON),
                        ('cursor', wt.HANDLE), ('brush', wt.HBRUSH), ('menu', wt.LPCWSTR), ('name', wt.LPCWSTR)]
        class WindowPos(ctypes.Structure):
            _fields_ = [('hwnd', wt.HWND), ('after', wt.HWND), ('x', ctypes.c_int), ('y', ctypes.c_int),
                        ('cx', ctypes.c_int), ('cy', ctypes.c_int), ('flags', wt.UINT)]
        class PaintStruct(ctypes.Structure):
            _fields_ = [('hdc', wt.HDC), ('erase', wt.BOOL), ('rect', wt.RECT), ('restore', wt.BOOL),
                        ('update', wt.BOOL), ('reserved', ctypes.c_byte*32)]
        self.WindowPos, self.PaintStruct = WindowPos, PaintStruct
        signatures = {
            'GetAncestor': ([wt.HWND, wt.UINT], wt.HWND),
            'GetClientRect': ([wt.HWND, ctypes.POINTER(wt.RECT)], wt.BOOL),
            'GetWindowRect': ([wt.HWND, ctypes.POINTER(wt.RECT)], wt.BOOL),
            'GetDC': ([wt.HWND], wt.HDC), 'ReleaseDC': ([wt.HWND, wt.HDC], ctypes.c_int),
            'GetWindowLongPtrW': ([wt.HWND, ctypes.c_int], ctypes.c_ssize_t),
            'SetWindowLongPtrW': ([wt.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t),
            'PrintWindow': ([wt.HWND, wt.HDC, wt.UINT], wt.BOOL),
            'GetForegroundWindow': ([], wt.HWND),
            'IsWindow': ([wt.HWND], wt.BOOL), 'IsIconic': ([wt.HWND], wt.BOOL),
            'RegisterClassW': ([ctypes.POINTER(WindowClass)], wt.WORD),
            'UnregisterClassW': ([wt.LPCWSTR, wt.HINSTANCE], wt.BOOL),
            'CreateWindowExW': ([wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p], wt.HWND),
            'DefWindowProcW': ([wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t], ctypes.c_ssize_t),
            'SetWindowPos': ([wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT], wt.BOOL),
            'ShowWindow': ([wt.HWND, ctypes.c_int], wt.BOOL), 'DestroyWindow': ([wt.HWND], wt.BOOL),
            'InvalidateRect': ([wt.HWND, ctypes.POINTER(wt.RECT), wt.BOOL], wt.BOOL),
            'RedrawWindow': ([wt.HWND, ctypes.c_void_p, wt.HANDLE, wt.UINT], wt.BOOL),
            'BeginPaint': ([wt.HWND, ctypes.POINTER(PaintStruct)], wt.HDC),
            'EndPaint': ([wt.HWND, ctypes.POINTER(PaintStruct)], wt.BOOL),
        }
        for name, (args, result) in signatures.items():
            method = getattr(self.user, name)
            method.argtypes, method.restype = args, result
        gdi_signatures = {
            'CreateCompatibleDC': ([wt.HDC], wt.HDC),
            'CreateCompatibleBitmap': ([wt.HDC, ctypes.c_int, ctypes.c_int], wt.HBITMAP),
            'SelectObject': ([wt.HDC, wt.HANDLE], wt.HANDLE),
            'DeleteObject': ([wt.HANDLE], wt.BOOL), 'DeleteDC': ([wt.HDC], wt.BOOL),
            'BitBlt': ([wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HDC,
                        ctypes.c_int, ctypes.c_int, wt.DWORD], wt.BOOL),
            'StretchBlt': ([wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HDC,
                            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.DWORD], wt.BOOL),
            'SetStretchBltMode': ([wt.HDC, ctypes.c_int], ctypes.c_int),
            'SetBrushOrgEx': ([wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p], wt.BOOL),
            'GetPixel': ([wt.HDC, ctypes.c_int, ctypes.c_int], wt.DWORD),
        }
        for name, (args, result) in gdi_signatures.items():
            method = getattr(self.gdi, name)
            method.argtypes, method.restype = args, result
        self.comctl.SetWindowSubclass.argtypes = [wt.HWND, self.sub_type, ctypes.c_size_t, ctypes.c_size_t]
        self.comctl.SetWindowSubclass.restype = wt.BOOL
        self.comctl.RemoveWindowSubclass.argtypes = [wt.HWND, self.sub_type, ctypes.c_size_t]
        self.comctl.RemoveWindowSubclass.restype = wt.BOOL
        self.comctl.DefSubclassProc.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
        self.comctl.DefSubclassProc.restype = ctypes.c_ssize_t
        kernel.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        kernel.GetModuleHandleW.restype = wt.HMODULE
        self.instance = kernel.GetModuleHandleW(None)
        self.hwnd = self.user.GetAncestor(root.winfo_id(), 2)
        # Tk's top-level normally lets its client paint underneath child
        # windows. During a live resize that creates a second paint stream:
        # the retained-frame overlay and Tk can reach the compositor in
        # different orders, exposing a partial frame for one scanout. Clip
        # the parent around children and make the retained-frame child
        # composited so every overlay paint is presented as one bitmap.
        self._window_style_offset = -16
        self._old_window_style = self.user.GetWindowLongPtrW(self.hwnd, self._window_style_offset)
        self._window_style_changed = False
        if not (self._old_window_style & 0x02000000):  # WS_CLIPCHILDREN
            self.user.SetWindowLongPtrW(self.hwnd, self._window_style_offset,
                                        self._old_window_style | 0x02000000)
            self._window_style_changed = True
        # Tk paints the parent and its children as separate streams. During a
        # live resize that lets a parent erase/paint reach the compositor
        # between two retained-frame paints. Make the top-level subtree use
        # one back buffer while the retained frame is visible. This is the
        # window-level part of the atomic presentation contract; the overlay
        # already has WS_EX_COMPOSITED, but that alone does not order the
        # parent's paint stream.
        self._window_exstyle_offset = -20
        self._old_window_exstyle = self.user.GetWindowLongPtrW(self.hwnd, self._window_exstyle_offset)
        self._window_exstyle_changed = False
        if not (self._old_window_exstyle & 0x02000000):  # WS_EX_COMPOSITED
            self.user.SetWindowLongPtrW(self.hwnd, self._window_exstyle_offset,
                                        self._old_window_exstyle | 0x02000000)
            self._window_exstyle_changed = True
        self.overlay = self.atom = self.dc = self.bitmap = self.previous_bitmap = None
        self.source_size = (0, 0)
        self.target_size = (0, 0)
        self.ready = self.visible = self.active = self.closed = False
        self.version = 0
        self.last_change = 0.
        self.captures = self.paints = self.commits = 0
        self.sizing_sessions = 0
        self.paint_samples = deque(maxlen=1024)
        self.last_error = None
        self.name = f'Visual2CraftResize_{os.getpid()}_{id(self)}'
        self.wnd_proc = self.wnd_type(self._overlay_message)
        self.sub_proc = self.sub_type(self._window_message)
        self.subclass_id = id(self)
        window_class = WindowClass(proc=self.wnd_proc, instance=self.instance, name=self.name)
        self.atom = self.user.RegisterClassW(ctypes.byref(window_class))
        if not self.atom:
            raise ctypes.WinError(ctypes.get_last_error())
        # Child, clip siblings, no background brush; visibility begins only after capture.
        # WS_EX_NOACTIVATE | WS_EX_COMPOSITED. The latter makes the GDI
        # StretchBlt paint land in a back buffer before Windows exposes it.
        self.overlay = self.user.CreateWindowExW(0x0A000000, self.name, '', 0x44000000,
                                                 0, 0, 1, 1, self.hwnd, None, self.instance, None)
        if not self.overlay or not self.comctl.SetWindowSubclass(self.hwnd, self.sub_proc, self.subclass_id, 0):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)
        _LIVE[self.hwnd] = self

    def client_size(self):
        rect = wt.RECT()
        self.user.GetClientRect(self.hwnd, ctypes.byref(rect))
        return max(1, rect.right), max(1, rect.bottom)

    def _capture(self):
        if self.visible:
            return True
        width, height = self.client_size()
        if width < 2 or height < 2:
            return False
        source = self.user.GetDC(self.hwnd)
        if not source:
            return False
        try:
            self.dc = self.gdi.CreateCompatibleDC(source)
            self.bitmap = self.gdi.CreateCompatibleBitmap(source, width, height)
            if not self.dc or not self.bitmap:
                raise ctypes.WinError(ctypes.get_last_error())
            self.previous_bitmap = self.gdi.SelectObject(self.dc, self.bitmap)
            if not self.user.PrintWindow(self.hwnd, self.dc, 3):  # client only, render full content
                # A visible-only capture is safe only for our foreground window.
                foreground = self.user.GetAncestor(self.user.GetForegroundWindow(), 2)
                if foreground != self.hwnd or not self.gdi.BitBlt(self.dc, 0, 0, width, height, source, 0, 0, 0x40CC0020):
                    raise ctypes.WinError(ctypes.get_last_error())
            self.source_size = (width, height)
            self.target_size = self.source_size
            self.captures += 1
            self.visible = True
            self._fit()
            return True
        except OSError as error:
            self.last_error = str(error)
            self._release_bitmap()
            return False
        finally:
            self.user.ReleaseDC(self.hwnd, source)

    def _fit(self):
        if self.visible and self.overlay:
            width, height = self.client_size()
            self.user.SetWindowPos(self.overlay, None, 0, 0, width, height, 0x0050)  # no activate, show
            self.user.InvalidateRect(self.overlay, None, False)

    def _fit_proposed(self, outer_width, outer_height):
        """Resize the retained child before the parent commits its new size."""
        if not self.visible or not self.overlay:
            return
        outer = wt.RECT()
        self.user.GetWindowRect(self.hwnd, ctypes.byref(outer))
        client_width, client_height = self.client_size()
        frame_width = max(0, (outer.right - outer.left) - client_width)
        frame_height = max(0, (outer.bottom - outer.top) - client_height)
        width = max(1, int(outer_width) - frame_width)
        height = max(1, int(outer_height) - frame_height)
        self.user.SetWindowPos(self.overlay, None, 0, 0, width, height, 0x0050)
        self.user.InvalidateRect(self.overlay, None, False)

    def _window_message(self, hwnd, message, wp, lp, _id, _data):
        # No Tk calls, including after()/event_generate(), are allowed here.
        if not self.closed:
            try:
                if message == 0x0046 and self.ready and not self.user.IsIconic(hwnd):  # WINDOWPOSCHANGING
                    pos = ctypes.cast(lp, ctypes.POINTER(self.WindowPos)).contents
                    rect = wt.RECT()
                    self.user.GetWindowRect(hwnd, ctypes.byref(rect))
                    if not pos.flags & 1 and pos.cx > 1 and pos.cy > 1 and (pos.cx, pos.cy) != (rect.right-rect.left, rect.bottom-rect.top):
                        self._capture()
                        # The parent has not changed size yet. Resize the
                        # retained child against the proposed client rect so
                        # the newly exposed strip is covered on the same
                        # native transaction instead of one paint later.
                        self._fit_proposed(pos.cx, pos.cy)
                elif message == 0x0214:  # SIZING; title-bar moves never enter this state.
                    if not self.active:
                        self.sizing_sessions += 1
                    self.active = True
                elif message in (0x0232, 0x001F):  # EXITSIZEMOVE / CANCELMODE
                    self.active = False
                    self.last_change = time.monotonic()
                elif message == 0x0014 and self.visible:  # WM_ERASEBKGND
                    # Erasing the parent while the retained child is being
                    # resized exposes a blank scanline between paint passes.
                    return 1
            except Exception as error:
                self.last_error = str(error)
        result = self.comctl.DefSubclassProc(hwnd, message, wp, lp)
        # Tk's wrapper handles WINDOWPOSCHANGED itself and may not emit SIZE.
        # Read the final client rectangle only after that handler has run.
        if not self.closed and self.visible and message in (0x0047, 0x0005):
            size = self.client_size()
            if size != self.target_size:
                self.target_size = size
                self.version += 1
                self.last_change = time.monotonic()
                self._fit()
        if message == 0x0082:  # NCDESTROY, defensive cleanup even if App._close wasn't used.
            self.close()
            _LIVE.pop(hwnd, None)
        return result

    def _overlay_message(self, hwnd, message, wp, lp):
        if message == 0x0082 and hwnd == self.overlay:
            self.overlay = None
        if message == 0x0014:  # ERASING would expose blank/background blocks during resize.
            return 1
        if message == 0x000F:
            started = time.perf_counter()
            paint = self.PaintStruct()
            hdc = self.user.BeginPaint(hwnd, ctypes.byref(paint))
            try:
                if self.dc and self.bitmap:
                    rect = wt.RECT()
                    self.user.GetClientRect(hwnd, ctypes.byref(rect))
                    self.gdi.SetStretchBltMode(hdc, 4)  # HALFTONE, consistent full-frame scaling.
                    self.gdi.SetBrushOrgEx(hdc, 0, 0, None)
                    ok = self.gdi.StretchBlt(hdc, 0, 0, rect.right, rect.bottom, self.dc,
                                             0, 0, *self.source_size, 0x00CC0020)
                    if not ok:
                        self.last_error = str(ctypes.WinError(ctypes.get_last_error()))
                    self.paints += 1
                    self.paint_samples.append((time.perf_counter(), (time.perf_counter()-started)*1000,
                                               rect.right, rect.bottom))
            finally:
                self.user.EndPaint(hwnd, ctypes.byref(paint))
            return 0
        return self.user.DefWindowProcW(hwnd, message, wp, lp)

    def commit(self, version):
        """Called on the normal Tk UI thread only after its final frame is ready."""
        if not self.visible or self.active or version != self.version:
            return False
        self.user.ShowWindow(self.overlay, 0)
        self.visible = False
        self.user.RedrawWindow(self.hwnd, None, None, 0x0181)  # invalidate, all children, update now
        self.commits += 1
        self._release_bitmap()
        return True

    def _release_bitmap(self):
        if self.dc and self.previous_bitmap:
            self.gdi.SelectObject(self.dc, self.previous_bitmap)
        if self.bitmap:
            self.gdi.DeleteObject(self.bitmap)
        if self.dc:
            self.gdi.DeleteDC(self.dc)
        self.dc = self.bitmap = self.previous_bitmap = None

    def statistics(self):
        return {'captures': self.captures, 'paints': self.paints, 'commits': self.commits,
                'sizing_sessions': self.sizing_sessions,
                'version': self.version, 'visible': self.visible, 'active': self.active,
                'source_size': self.source_size, 'target_size': self.target_size,
                'last_error': self.last_error, 'paint_samples': list(self.paint_samples)}

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.active = self.visible = False
        removed = self.comctl.RemoveWindowSubclass(self.hwnd, self.sub_proc, self.subclass_id)
        if removed or not self.user.IsWindow(self.hwnd):
            _LIVE.pop(self.hwnd, None)
        if self.overlay:
            overlay = self.overlay
            self.overlay = None
            self.user.DestroyWindow(overlay)
        self._release_bitmap()
        if self.atom:
            self.user.UnregisterClassW(self.name, self.instance)
            self.atom = None
        if self._window_style_changed and self.user.IsWindow(self.hwnd):
            self.user.SetWindowLongPtrW(self.hwnd, self._window_style_offset, self._old_window_style)
            self._window_style_changed = False
        if self._window_exstyle_changed and self.user.IsWindow(self.hwnd):
            self.user.SetWindowLongPtrW(self.hwnd, self._window_exstyle_offset, self._old_window_exstyle)
            self._window_exstyle_changed = False
