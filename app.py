"""Responsive, single-window Visual2Craft desktop studio."""
from __future__ import annotations

import ctypes
import math
import multiprocessing as mp
import os
from pathlib import Path
import queue
import random
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

from PIL import Image, ImageEnhance, ImageTk

from chatgpt_auth import ChatGPTAuth
from video2pack import DEFAULT_FRAMES, MAX_FRAMES, run
from video_preview import VideoPreview
from ui_timing import FrameTimer, WindowTimerResolution
from ui_worker import RasterWorker
from ui_widgets import (ACCENT, BG, BORDER, CARD, FIELD, MUTED, WHITE,
                        GlassFrame, GlassLabel, RoundedButton, RoundedField, RoundedSurface, parent_pixels)

GAME_COMMANDS = ('/reload', '/function vidbuild:check',
                 '/function vidbuild:build', '/function vidbuild:undo')


def _enable_windows_dpi_awareness():
    if os.name == 'nt':
        try:
            if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return 'per-monitor-v2'
        except (AttributeError, OSError):
            pass
    return 'default'


WINDOWS_DPI_MODE = _enable_windows_dpi_awareness()


def _dark_titlebar(window):
    """Style this app's standard Windows frame without changing OS settings."""
    if os.name != 'nt':
        return False
    try:
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetAncestor.restype = ctypes.c_void_p
        hwnd = user32.GetAncestor(window.winfo_id(), 2)
        dwm = ctypes.WinDLL('dwmapi', use_last_error=True)
        dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        enabled = ctypes.c_int(1)
        dark = dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(enabled), ctypes.sizeof(enabled)) == 0
        caption = ctypes.c_uint(0x00120F0C)  # COLORREF: #0C0F12.
        text = ctypes.c_uint(0x00FFFFFF)
        colored = dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption)) == 0
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), ctypes.sizeof(text))
        user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x37)
        return dark or colored
    except (AttributeError, OSError):
        return False


def output_snapshot(path):
    """Detect destination changes while a model request is running."""
    try:
        stat = Path(path).stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
    except FileNotFoundError:
        return None


def available_output(video, directory):
    stem = Path(video).stem if video else 'video_build'
    candidate = Path(directory) / f'{stem}.zip'
    number = 2
    while candidate.exists():
        candidate = Path(directory) / f'{stem}_{number}.zip'
        number += 1
    return str(candidate)


def _generation_worker(params, messages):
    """A terminable worker writes only to the parent's temporary directory."""
    try:
        if params.get('auth_mode') == 'chatgpt':
            params['access_token'] = ChatGPTAuth().access_token()
        result = run(**params, progress=lambda value: messages.put(('progress', value)))
        messages.put(('result', result))
    except Exception as exc:
        messages.put(('error', str(exc)))


class App(tk.Tk):
    @property
    def _render_pending(self):
        return self._renderer.pending

    @property
    def _presentation_pending(self):
        return self._native_resize is not None and self._native_resize.visible

    def mainloop(self, n=0):
        self._animation_clock.enable_precise_driver()
        try:
            super().mainloop(n)
        finally:
            self._animation_clock.close()

    def __init__(self):
        super().__init__()
        self._timer_resolution = WindowTimerResolution(self)
        self._particle_timer = FrameTimer(self)
        self._particle_last = time.monotonic()
        self._particle_items = {}
        self.tk.eval('''
            namespace eval ::Visual2Craft {}
            proc ::Visual2Craft::particles {canvas moves hidden} {
                foreach change $moves {
                    lassign $change item x0 y0 x1 y1 color
                    $canvas coords $item $x0 $y0 $x1 $y1
                    if {$color ne ""} {$canvas itemconfigure $item -fill $color -state normal}
                }
                foreach item $hidden {$canvas itemconfigure $item -state hidden}
            }
        ''')
        self.title('Visual2Craft')
        self.configure(bg=BG)
        self._style = ttk.Style(self)
        if 'clam' in self._style.theme_names():
            try:
                self._style.theme_use('clam')
            except tk.TclError:
                pass
        self._style.configure('TCombobox', fieldbackground=FIELD, background=FIELD, foreground=WHITE)
        self._style.map('TCombobox', fieldbackground=[('readonly', FIELD), ('disabled', FIELD)],
                        background=[('readonly', FIELD), ('disabled', FIELD)],
                        foreground=[('readonly', WHITE), ('disabled', MUTED)])
        self._dpi_scale = max(1, self.winfo_fpixels('1i') / 96)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f'{min(round(1200*self._dpi_scale), sw-80)}x{min(round(800*self._dpi_scale), sh-100)}')
        self.minsize(min(round(960*self._dpi_scale), sw), min(round(620*self._dpi_scale), sh-80))
        if os.name == 'nt':
            self.state('zoomed')
        try:
            self._icon = tk.PhotoImage(file=str(Path(__file__).parent/'assets/Visual2Craft_icon.png'))
            self.iconphoto(True, self._icon)
        except (tk.TclError, OSError):
            pass
        self.auth = ChatGPTAuth()
        self.video = tk.StringVar()
        self.output = tk.StringVar(value=available_output(None, Path.home()/'Downloads'))
        self._suggested_output = self.output.get()
        self.provider = tk.StringVar(value='chatgpt')
        self.api_model = tk.StringVar(value='gpt-5.5')
        self.api_key = tk.StringVar(value=os.getenv('OPENAI_API_KEY', ''))
        self.model = tk.StringVar()
        self.frames = tk.IntVar(value=DEFAULT_FRAMES)
        self.building_hints = tk.StringVar()
        self.account_choice = tk.StringVar()
        self.account_ids = {}
        self.model_mapping = {}
        self.status = tk.StringVar(value='Select a video, connect your account, and choose a model.')
        self._busy = False
        self._signin_cancel = None
        self._task_kind = None
        self._task_started = None
        self._ui_queue = queue.Queue()
        self._process = self._process_queue = self._temp_output = None
        self._pending_result = None
        self._confirmation = None
        self._content = 'video'
        self._last_result = None
        self._error_details = ''
        self._resize_after = None
        self._resize_busy = False
        self._last_window_size = None
        self._layout_signature = None
        self._closing = False
        self._renderer = RasterWorker(self)
        self._paint_timer = FrameTimer(self)
        self._paint_step_id = None
        self._layout_after = None
        self._geometry_pending = False
        self._allow_glass_paint = False
        self._initial_paint = True
        self._glass_paint_queue = []
        self._waiting_surface = None
        self._native_resize = None
        self._native_resize_seen = self._native_resize_target = -1
        self._native_resize_force = False
        self._resize_commit_after = None
        self._glass_widgets = []
        self._glass_after = None
        self._deferred_glass_refresh = False
        self._particle_tick = 0
        self._preview_bar_bounds = None
        self._preview_progress_updating = False
        rng = random.Random(153)
        self._particles = [dict(side=side, x=rng.uniform(.002, .11), y=rng.uniform(.70, 1.02),
                                speed=rng.uniform(.00045, .0012), size=rng.uniform(1.1, 2.7),
                                phase=rng.uniform(0, math.tau))
                           for side in ('left', 'right') for _ in range(24)]
        self._build_ui()
        self._refresh_account_choices()
        self._provider_changed()
        self.bind('<Control-o>', lambda _: self.choose_video())
        self.bind('<Control-s>', lambda _: self.choose_output())
        self.bind('<Control-comma>', lambda _: self._focus_settings())
        self.bind('<Escape>', lambda _: self._close_overlay())
        self.protocol('WM_DELETE_WINDOW', self._close)
        self.after(50, self._drain_ui_queue)
        self.after(250, self._update_elapsed)
        self._particle_timer.schedule(self._animate_particles)
        self.bind('<Map>', self._mapped_titlebar, add='+')
        self.after(180, self._apply_titlebar)

    def _mapped_titlebar(self, event):
        if event.widget is self and not self._closing:
            self.after_idle(self._apply_titlebar)

    def _apply_titlebar(self):
        self._titlebar_dark = _dark_titlebar(self)
        if os.name == 'nt' and self._native_resize is None and not self._closing:
            try:
                from ui_resize import ResizePresentation
                self._native_resize = ResizePresentation(self)
                self._native_resize.ready = not self._initial_paint
            except (OSError, AttributeError):
                pass

    def _freeze_resize_work(self):
        """Keep one coherent visible frame while hidden widgets are changing."""
        self._resize_busy = self._geometry_pending = True
        self._renderer.cancel_all()
        self._glass_paint_queue.clear()
        self._waiting_surface = None
        for name in ('_layout_after', '_paint_step_id'):
            token = getattr(self, name)
            if token is not None:
                self._paint_timer.cancel(token)
                setattr(self, name, None)
        for name in ('_resize_after', '_glass_after', '_resize_commit_after'):
            token = getattr(self, name)
            if token is not None:
                self.after_cancel(token)
                setattr(self, name, None)

    def _sync_resize_presentation(self):
        native = self._native_resize
        if native is None or not native.visible or self._closing:
            return
        if self._native_resize_seen != native.version:
            self._native_resize_seen = native.version
            self._freeze_resize_work()
        if native.active or time.monotonic()-native.last_change < .06:
            return
        if self._native_resize_target != native.version:
            self._native_resize_target = native.version
            self._native_resize_force = True
            self._last_window_size = None
            event = tk.Event()
            event.widget = self
            event.width, event.height = self.winfo_width(), self.winfo_height()
            try:
                self._schedule_background(event)
            finally:
                self._native_resize_force = False

    def _commit_resize_presentation(self):
        self._resize_commit_after = None
        native = self._native_resize
        if self._closing or native is None or not native.visible:
            return
        if native.active or native.version != self._native_resize_target:
            return
        pending = (self._resize_busy or self._geometry_pending or self._renderer.pending
                   or self._glass_paint_queue or self._waiting_surface is not None
                   or self._glass_after is not None or self._paint_step_id is not None)
        if not pending and self.preview.decoder and self.preview.winfo_ismapped():
            # A live/paused video must also have a complete image at the new size.
            pending = self.preview._surface.size != (self.preview.winfo_width(), self.preview.winfo_height())
        if pending:
            self._resize_commit_after = self.after(10, self._commit_resize_presentation)
            return
        self.update_idletasks()
        if not self._geometry_pending and native.version == self._native_resize_target:
            native.commit(self._native_resize_target)

    def _font(self, size=14, bold=False):
        return ('Segoe UI', -round(size*self._dpi_scale), 'bold' if bold else 'normal')

    def _u(self, value):
        return round(value*self._dpi_scale)

    def _label(self, parent, text='', size=14, bold=False, **kwargs):
        return GlassLabel(parent, text=text, bg=parent.cget('bg'), fg=WHITE, font=self._font(size, bold),
                        anchor='w', justify='left', **kwargs)

    def _button(self, parent, text, action, primary=False, danger=False, compact=False, micro=False):
        return RoundedButton(parent, text, action, font=self._font(10 if micro else 11 if compact else 13, primary),
                             scale=self._dpi_scale, primary=primary, danger=danger, compact=compact, micro=micro)

    def _entry(self, parent, variable, show=None):
        return RoundedField(parent, textvariable=variable, show=show,
                            font=self._font(), scale=self._dpi_scale)

    def _build_ui(self):
        from ui_layout import build_reference_ui
        build_reference_ui(self, GAME_COMMANDS)

    def _schedule_background(self, event):
        if event.widget is not self or self._closing:
            return
        native = self._native_resize
        if native is not None and native.visible and not self._native_resize_force:
            if self._native_resize_seen != native.version:
                self._native_resize_seen = native.version
                self._freeze_resize_work()
            return
        size = (event.width, event.height)
        # Windows sends Configure on MOVE as well as on resize. The scenery is
        # relative to the app; moving must not relayout every control or blur it.
        if size == self._last_window_size:
            return
        self._last_window_size = size
        self._resize_busy = True
        self._geometry_pending = True
        self._renderer.cancel_all()
        self._glass_paint_queue.clear()
        self._waiting_surface = None
        if self._paint_step_id is not None:
            self._paint_timer.cancel(self._paint_step_id)
            self._paint_step_id = None
        if self._glass_after is not None:
            self.after_cancel(self._glass_after)
            self._glass_after = None
        # Coalesce a burst of Configure events into one layout per UI frame.
        if self._layout_after is None:
            self._layout_after = self._paint_timer.schedule(self._layout_latest)
        if self._resize_after is None:
            self._resize_after = self.after(40, self._draw_background)

    def _layout_latest(self):
        self._layout_after = None
        if self._closing:
            return
        from ui_layout import layout_reference_ui
        layout_reference_ui(self)
        self._geometry_pending = False

    def _draw_background(self):
        self._resize_after = None
        width, height = self.winfo_width(), self.winfo_height()
        if width < 2 or height < 2 or self._closing:
            return
        source = self._scene_source
        if (getattr(self, '_scene_render_source', None) is source
                and getattr(self, '_scene_pixels', None) is not None
                and self._scene_pixels.size == (width, height)):
            self._paint_glass()
            return
        def render():
            pixels = source if source.mode == 'RGB' else source.convert('RGB')
            scale = max(width/pixels.width, height/pixels.height)
            image = pixels.resize((max(width, round(pixels.width*scale)), max(height, round(pixels.height*scale))),
                                  Image.Resampling.BILINEAR)
            x, y = (image.width-width)//2, (image.height-height)//2
            return ImageEnhance.Brightness(image.crop((x, y, x+width, y+height))).enhance(.84)
        def apply(image):
            if self._closing:
                return
            if (self.winfo_width(), self.winfo_height()) != (width, height):
                if self._resize_after is None:
                    self._resize_after = self.after(1, self._draw_background)
                return
            self._scene_pixels = image
            self._scene_render_source = source
            self._scene_photo = ImageTk.PhotoImage(image, master=self)
            self.canvas.itemconfigure(self._background_item, image=self._scene_photo)
            self._paint_glass()
        self._renderer.submit('scene', render, apply)

    def _queue_glass_refresh(self):
        if self._resize_busy:
            self._deferred_glass_refresh = True
            return
        if not self._closing and not self._resize_busy and self._glass_after is None:
            self._glass_after = self.after(90, self._paint_glass)

    def _paint_glass(self):
        if self._glass_after:
            self.after_cancel(self._glass_after)
        self._glass_after = None
        if self._closing or self._geometry_pending or not hasattr(self, '_scene_pixels'):
            return
        self._glass_widgets = [widget for widget in self._glass_widgets if widget.winfo_exists()]
        self._glass_paint_queue = sorted(self._glass_widgets, key=lambda item: item._w.count('.'))
        self._waiting_surface = None
        self._schedule_glass_step()

    def _schedule_glass_step(self):
        if not self._closing and self._paint_step_id is None:
            self._paint_step_id = self._paint_timer.schedule(self._paint_glass_step)

    def _surface_painted(self, widget):
        if self._waiting_surface is widget:
            self._waiting_surface = None
            self._schedule_glass_step()

    def _paint_glass_step(self):
        self._paint_step_id = None
        if self._closing or self._geometry_pending:
            return
        if self._waiting_surface is not None:
            return
        # Before the first complete frame, fill more of each idle slice so the
        # initial screen appears sooner. Interactive refreshes keep a 3 ms budget.
        deadline = time.perf_counter()+(.008 if self._initial_paint else .003)
        while self._glass_paint_queue:
            widget = self._glass_paint_queue.pop(0)
            if widget.winfo_exists() and widget.winfo_ismapped():
                # The global bottom-up refresh supersedes individually queued paints.
                # Cancel them to avoid drawing each glass pane a second time.
                timer_attr = '_chrome_id' if isinstance(widget, RoundedButton) else '_draw_id'
                timer_id = getattr(widget, timer_attr, None)
                if timer_id is not None:
                    widget.after_cancel(timer_id)
                    setattr(widget, timer_attr, None)
                self._allow_glass_paint = True
                try:
                    widget._draw_chrome() if isinstance(widget, RoundedButton) else widget._draw()
                finally:
                    self._allow_glass_paint = False
                if getattr(widget, '_raster_pending', False):
                    self._waiting_surface = widget
                    return
                if time.perf_counter() >= deadline:
                    self._schedule_glass_step()
                    return
        self._settings_photo = ImageTk.PhotoImage(parent_pixels(self.settings_canvas), master=self)
        self.settings_canvas.itemconfigure(self._settings_background_item, image=self._settings_photo)
        self.settings_canvas.coords(self._settings_background_item,
                                    self.settings_canvas.canvasx(0), self.settings_canvas.canvasy(0))
        self.settings_canvas.tag_lower(self._settings_background_item)
        self._resize_busy = False
        self._initial_paint = False
        self.preview.refresh_backdrop()
        if self._deferred_glass_refresh:
            self._deferred_glass_refresh = False
            self._paint_glass()
            return
        if self._native_resize is not None:
            self._native_resize.ready = True
            if self._native_resize.visible and self._resize_commit_after is None:
                self._resize_commit_after = self.after_idle(self._commit_resize_presentation)

    def _status_changed(self, *_):
        from ui_layout import layout_feedback
        if layout_feedback(self):
            self._queue_glass_refresh()
        # The first layout may precede the label's requested-height update.
        # Coalesce that follow-up rather than compositing all panels on each
        # status message sent from the worker.
        if getattr(self, '_feedback_after', None) is None:
            self._feedback_after = self.after_idle(self._finish_feedback_layout)

    def _finish_feedback_layout(self):
        self._feedback_after = None
        if not self._closing:
            from ui_layout import layout_feedback
            if layout_feedback(self):
                self._queue_glass_refresh()

    def _scroll_form(self, *args):
        self.settings_canvas.yview(*args)
        self._queue_glass_refresh()

    def _ensure_settings_visible(self, event):
        widget = event.widget
        node = widget
        while node is not None and node is not self.form:
            node = getattr(node, 'master', None)
        if node is None:
            return
        def reveal():
            if self._closing or not widget.winfo_exists():
                return
            top = widget.winfo_rooty()-self.form.winfo_rooty()
            bottom = top+widget.winfo_height()
            current = self.settings_canvas.canvasy(0)
            height = self.settings_canvas.winfo_height()
            target = top-self._u(6) if top < current else bottom-height+self._u(6) if bottom > current+height else current
            if target != current:
                total = max(1, self.form.winfo_height())
                self.settings_canvas.yview_moveto(max(0, min(target, total-height))/total)
                self._queue_glass_refresh()
        self.after_idle(reveal)

    def _scroll_settings(self, event):
        widget = event.widget
        while widget:
            if widget is self.right:
                self.settings_canvas.yview_scroll(-int(event.delta/120), 'units')
                self._queue_glass_refresh()
                return
            widget = getattr(widget, 'master', None)

    def _animate_particles(self):
        if self._closing:
            return
        self._sync_resize_presentation()
        if self._native_resize is not None and self._native_resize.visible:
            self._particle_last = time.monotonic()
            self._particle_timer.schedule(self._animate_particles)
            return
        now = time.monotonic()
        elapsed = min(.125, now-self._particle_last)
        self._particle_last = now
        if self.state() != 'iconic':
            self._particle_tick += elapsed*8
            for particle in self._particles:
                particle['y'] -= particle['speed']*elapsed*8
                if particle['y'] < .68:
                    particle['y'] = 1.02
            targets = [self.canvas]
            for pane_name in ('left_card', 'right_card', 'prompt_card'):
                pane = getattr(self, pane_name, None)
                if isinstance(pane, (GlassFrame, RoundedSurface)):
                    targets.append(pane.canvas)
            width, height = self.winfo_width(), self.winfo_height()
            root_x, root_y = self.winfo_rootx(), self.winfo_rooty()
            drift_span = self._u(4)
            prepared = []
            for particle in self._particles:
                drift = math.sin(self._particle_tick*.045+particle['phase'])*drift_span
                px = particle['x']*width if particle['side'] == 'left' else (1-particle['x'])*width
                bright = math.sin(self._particle_tick*.11+particle['phase'])
                color = '#CF7AFF' if bright > .3 else '#9253C5' if bright > -.4 else '#5D397C'
                prepared.append((px+drift, particle['y']*height, max(2, self._u(particle['size'])), color))
            for canvas in targets:
                if not canvas.winfo_exists() or not canvas.winfo_ismapped():
                    continue
                items = self._particle_items.setdefault(canvas, {})
                x0, y0 = canvas.winfo_rootx()-root_x, canvas.winfo_rooty()-root_y
                cw, ch = canvas.winfo_width(), canvas.winfo_height()
                if y0+ch < height*.68 or x0 > width*.14 and x0+cw < width*.86:
                    continue
                moves, hidden = [], []
                for index, (px, py, size, color) in enumerate(prepared):
                    x, y = px-x0, py-y0
                    if -8 <= x <= cw+8 and -8 <= y <= ch+8:
                        if index not in items:
                            items[index] = [canvas.create_rectangle(x, y, x+size, y+size, fill=color,
                                                                   outline='', tags='nether_particle'), color, True]
                        else:
                            item, old_color, visible = items[index]
                            moves.append((item, x, y, x+size, y+size, color if color != old_color or not visible else ''))
                            items[index][1:] = [color, True]
                    elif index in items and items[index][2]:
                        hidden.append(items[index][0])
                        items[index][2] = False
                if moves or hidden:
                    self.tk.call('::Visual2Craft::particles', canvas._w, tuple(moves), tuple(hidden))
            self._particle_timer.schedule(self._animate_particles)
        else:
            self.after(120, self._animate_particles)

    def _focus_settings(self):
        self.settings_canvas.yview_moveto(0)
        self.mode_buttons[0].focus_set()

    def _provider_changed(self):
        if self._busy:
            return
        if self.provider.get() != 'chatgpt':
            self._cancel_sign_in()
            self._refresh_account_choices()
        self._cancel_confirmation()
        for button in self.mode_buttons:
            button.set_selected(button.mode_value == self.provider.get())
        from ui_layout import layout_reference_ui
        layout_reference_ui(self)

    def _select_provider(self, value):
        if not self._busy:
            self.provider.set(value)
            self._provider_changed()

    def _layout_confirmation(self, _event=None):
        width = max(self._u(180), self.confirm_frame.winfo_width()-self._u(28))
        self.confirm_label.configure(wraplength=width)
        height = self.confirm_label.winfo_reqheight()+self.confirm_actions.winfo_reqheight()+self._u(38)
        if int(self.confirm_frame.cget('height')) != height:
            self.confirm_frame.configure(height=height)

    def _close_overlay(self):
        if self._confirmation:
            self._cancel_confirmation()
        elif self._content != 'video':
            self._back_to_video()

    def _show_confirmation(self, text, label, callback, cancel=None):
        self._cancel_confirmation()
        self._confirmation = (callback, cancel)
        self.confirm_label.configure(text=text)
        self.confirm_yes.configure(text=label)
        sx = getattr(self, '_reference_scale', self._dpi_scale)
        sy = getattr(self, '_reference_y', self._dpi_scale)
        self.confirm_frame.place(x=round(30*sx), y=round(92*sy), width=round(852*sx))
        self.confirm_frame.lift()
        self.after_idle(self._layout_confirmation)
        self.confirm_yes.focus_set()

    def _accept_confirmation(self):
        if not self._confirmation:
            return
        callback, _ = self._confirmation
        self._confirmation = None
        self.confirm_frame.place_forget()
        callback()

    def _cancel_confirmation(self):
        pending, self._confirmation = self._confirmation, None
        self.confirm_frame.place_forget()
        if pending and pending[1]:
            pending[1]()
        if pending:
            self.status.set('Action cancelled. No saved account or destination ZIP was changed.')

    def _refresh_account_choices(self):
        self.account_ids = {}
        for saved in self.auth.accounts():
            label = saved.email or 'ChatGPT account'
            if label in self.account_ids:
                label += f' ({saved.client_id[:8]})'
            self.account_ids[label] = saved.client_id
        self.account_box['values'] = list(self.account_ids)
        selected = self.auth.selected()
        self.account_choice.set(next((label for label, cid in self.account_ids.items()
                                      if selected and cid == selected.client_id), ''))
        self.login_btn.configure(text='Reauthorize current' if selected else 'Sign in with ChatGPT')

    def _account_context_menu(self, event):
        if self._busy:
            return 'break'
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label='Remove selected account…', command=self._ask_delete_account)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return 'break'

    def _ask_delete_account(self):
        if self._busy:
            return
        cid = self.account_ids.get(self.account_choice.get())
        if not cid:
            self._notice('Select a saved account first.')
            return
        label = self.account_choice.get()
        self._show_confirmation(f'Remove {label} from this device?\nIts saved authorization will be deleted.',
                                'Remove account', lambda: self.delete_account(cid))
        self.status.set('Review the account above, then confirm removal or cancel.')

    def delete_account(self, client_id):
        if self._busy:
            return
        self._cancel_sign_in()
        try:
            self.auth.delete_account(client_id)
        except Exception as exc:
            self._error(str(exc))
            return
        self.model_mapping = {}
        self.model_box['values'] = ()
        self.model.set('')
        self._refresh_account_choices()
        self.status.set('Saved account removed from this device. Refresh models for the remaining account.')

    def switch_account(self):
        if self._busy:
            return
        self._cancel_confirmation()
        cid = self.account_ids.get(self.account_choice.get())
        if not cid:
            return
        self._cancel_sign_in()
        try:
            self.auth.select(cid)
        except Exception as exc:
            self._error(str(exc))
            return
        self.model_mapping = {}
        self.model.set('')
        self.model_box['values'] = ()
        self._task('Fetching models for the selected account…', self.auth.models, self._models_ready, kind='models')

    def choose_video(self):
        if self._busy:
            return
        path = filedialog.askopenfilename(parent=self, title='Choose a Minecraft building video',
                                          filetypes=[('Video files', '*.mp4 *.mov *.mkv *.avi'), ('All files', '*.*')])
        if path:
            self._cancel_confirmation()
            self.video.set(path)
            if self.output.get() == self._suggested_output:
                self._suggested_output = available_output(path, Path(self.output.get()).parent)
                self.output.set(self._suggested_output)
            self._preview_bar_bounds = None
            self._back_to_video()
            self.status.set(f'Selected video: {Path(path).name}')

    def clear_video(self):
        """Remove the selected input and its decoder, preserving output/options."""
        if self._busy:
            return
        self._cancel_confirmation()
        self.preview.stop()
        self.video.set('')
        self._preview_bar_bounds = None
        self._back_to_video()
        self.status.set('Video cleared. Select a new building video to continue.')

    def choose_output(self):
        if self._busy:
            return
        path = filedialog.asksaveasfilename(parent=self, title='Choose output Data Pack ZIP',
                                            initialdir=str(Path(self.output.get()).parent),
                                            initialfile=Path(self.output.get()).name,
                                            defaultextension='.zip', filetypes=[('ZIP files', '*.zip')])
        if path:
            self._cancel_confirmation()
            self.output.set(path)

    def _set_busy(self, kind):
        self._busy = True
        self._task_kind = kind
        self._task_started = time.monotonic()
        for widget in self._editable:
            widget.configure(state='disabled')
        self.account_box.configure(state='disabled')
        self.model_box.configure(state='disabled')
        labels = {'generate': 'Generating…', 'models': 'Fetching models…', 'signin': 'Signing in…'}
        self.generate_btn.configure(text=labels.get(kind, 'Working…'), state='disabled')
        self._status_changed()
        self.progress_bar.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        if kind == 'generate':
            self.progress_bar.start(mode='determinate', value=0.0)
            self.cancel_btn.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(self._u(8), 0))
        else:
            self.progress_bar.start(mode='indeterminate')

    def _task(self, label, work, success, kind='models'):
        if self._busy:
            return
        self._cancel_confirmation()
        self._set_busy(kind)
        self.status.set(label)
        def runner():
            try:
                value = work()
            except Exception as exc:
                self._ui_queue.put(('error', str(exc)))
            else:
                self._ui_queue.put(('success', (success, value)))
        threading.Thread(target=runner, daemon=True).start()

    def _ready(self):
        self._busy = False
        self._task_kind = None
        self._task_started = None
        self.progress_bar.stop()
        self.progress_bar.grid_remove()
        self.cancel_btn.grid_remove()
        self.elapsed_label.configure(text='')
        for widget in self._editable:
            widget.configure(state='normal')
        self.account_box.configure(state='readonly')
        self.model_box.configure(state='readonly')
        self.generate_btn.configure(text='Generate Data Pack', state='normal')
        self._status_changed()

    def _drain_ui_queue(self):
        # Bursts of sampling/progress messages must yield back to animation and
        # input. Preserve message ordering while bounding each drain's UI work.
        deadline = time.monotonic()+.003
        try:
            while time.monotonic() < deadline:
                kind, payload = self._ui_queue.get_nowait()
                if kind == 'signin':
                    attempt, account, error = payload
                    if attempt is not self._signin_cancel:
                        continue
                    self._cancel_sign_in()
                    self._refresh_account_choices()
                    if error:
                        self.status.set('ChatGPT sign-in failed: ' + error)
                    else:
                        self.refresh_models()
                elif kind == 'error':
                    self._error(payload)
                elif kind == 'success':
                    callback, value = payload
                    callback(value)
        except queue.Empty:
            pass
        if self._process_queue is not None:
            try:
                while self._process_queue is not None and time.monotonic() < deadline:
                    kind, payload = self._process_queue.get_nowait()
                    if kind == 'status':
                        self.status.set(payload)
                    elif kind == 'progress':
                        self._update_progress(payload)
                    elif kind == 'result':
                        self._generation_complete(payload)
                    elif kind == 'error':
                        self._cleanup_generation()
                        self._error(payload)
            except queue.Empty:
                if self._process is not None and self._process.exitcode is not None:
                    # Queue feeder messages are flushed before a normal worker exits.
                    self._cleanup_generation()
                    self._error('Generation worker stopped before returning a result; destination ZIP was not changed.')
        if not self._closing:
            self.after(5 if time.monotonic() >= deadline else 50, self._drain_ui_queue)

    def _update_elapsed(self):
        if self._busy and self._task_started:
            seconds = int(time.monotonic()-self._task_started)
            self.elapsed_label.configure(text=f'{seconds//60:02}:{seconds%60:02}')
        if not self._closing:
            self.after(500, self._update_elapsed)

    def _update_progress(self, payload):
        message, fraction = payload, None
        if isinstance(payload, dict):
            message = payload.get('message') or ''
            fraction = payload.get('fraction')
        if message:
            self.status.set(str(message))
        if fraction is not None:
            try:
                self.progress_bar.set_progress(float(fraction))
            except (TypeError, ValueError):
                pass

    def _cleanup_generation(self, keep_temp=False):
        if self._process is not None:
            if self._process.is_alive():
                self._process.terminate()
            if self._process.pid is not None:
                self._process.join(timeout=2)
            self._process.close()
            self._process = None
        if self._process_queue is not None:
            self._process_queue.close()
            self._process_queue = None
        if not keep_temp and self._temp_output is not None:
            self._temp_output.cleanup()
            self._temp_output = None
        if not keep_temp:
            self._pending_result = None

    def cancel_task(self):
        if self._task_kind != 'generate':
            return
        self._cleanup_generation()
        self._ready()
        self.status.set('Generation cancelled. Destination ZIP unchanged. Usage already submitted may still be billed.')

    def _error(self, details):
        self._ready()
        self._error_details = str(details)
        self._show_details('Operation details', self._error_details, 'error')
        self.status.set('Error: ' + self._error_details.splitlines()[0])

    def _notice(self, details):
        self.status.set('Missing input: ' + str(details))

    def sign_in(self, add=False):
        if self._busy or self._signin_cancel is not None:
            return
        self._cancel_confirmation()
        attempt = threading.Event()
        self._signin_cancel = attempt
        self.account_choice.set('')
        self.model_mapping = {}
        self.model.set('')
        self.model_box['values'] = ()
        self.login_btn.configure(state='disabled')
        self.new_account_btn.configure(state='disabled')
        self.status.set('Complete ChatGPT authorization in your browser, or select a saved account to continue.')
        def runner():
            try:
                account = self.auth.sign_in(add_account=add, cancel_event=attempt)
            except Exception as exc:
                self._ui_queue.put(('signin', (attempt, None, str(exc))))
            else:
                self._ui_queue.put(('signin', (attempt, account, None)))
        threading.Thread(target=runner, daemon=True).start()

    def _cancel_sign_in(self):
        if self._signin_cancel is not None:
            self._signin_cancel.set()
            self._signin_cancel = None
            self.login_btn.configure(state='normal')
            self.new_account_btn.configure(state='normal')

    def refresh_models(self):
        if not self.account_ids.get(self.account_choice.get()):
            self._notice('Select a saved account or complete ChatGPT sign-in first.')
            return
        self._task('Fetching available models…', self.auth.models, self._models_ready, kind='models')

    def _models_ready(self, models):
        previous = self.model.get()
        self.model_mapping = {f'{title} [{slug}]': slug for title, slug in models}
        entries = list(self.model_mapping)
        self.model_box['values'] = entries
        self.model.set(previous if previous in entries else next(
            (name for name in entries if 'gpt-6' in self.model_mapping[name].lower()), entries[0] if entries else ''))
        self._ready()
        self.status.set(f'Found {len(models)} available ChatGPT models.')

    def _copy_text(self, value, source_button=None):
        self.clipboard_clear()
        self.clipboard_append(value)
        # The status label is normally hidden. Provide immediate, local visual
        # feedback without moving the layout or interrupting keyboard focus.
        self.copy_toast.show('Copied to clipboard')
        if source_button is not None:
            source_button.flash_copied()
        self.status.set('Copied: ' + value)

    def _copy_details(self):
        self._copy_text(str(self._last_result['zip']) if self._content == 'result' else self._error_details,
                        source_button=self.detail_copy)

    def _show_details(self, title, text, content):
        self._content = content
        self.preview.suspend()
        self.preview.place_forget()
        self.preview_bar.place_forget()
        self.video_prompt.place(relx=.5, rely=.5, anchor='center', relwidth=.94)
        self.video_label.configure(text=title)
        self.browse_btn.grid_remove()
        self.video_formats.grid_remove()
        self.upload_tile.grid_remove()
        self.video_frame.rowconfigure(0, weight=0)
        self.video_frame.rowconfigure(3, weight=1)
        self.prompt_card.configure(height=self._u(64))
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', text)
        self.details.configure(state='disabled')
        self.details.grid(row=3, column=0, sticky='nsew', pady=10)
        self.detail_actions.grid(row=4, column=0, sticky='ew')
        self.detail_copy.configure(text='Copy ZIP path' if content == 'result' else 'Copy details')

    def _back_to_video(self):
        self._content = 'video'
        self.details.grid_remove()
        self.detail_actions.grid_remove()
        self.browse_btn.grid()
        self.video_formats.grid()
        self.upload_tile.grid()
        self.video_frame.rowconfigure(0, weight=1)
        self.video_frame.rowconfigure(3, weight=0)
        self.browse_btn.configure(text='Change video' if self.video.get() else 'Browse files')
        if self.video.get():
            self.video_prompt.place_forget()
            self.preview.place(x=0, y=0, relwidth=1, relheight=1)
            name = Path(self.video.get()).name
            self.preview_name.configure(text=name if len(name) <= 40 else name[:37]+'…')
            self.preview.load(self.video.get())
            self.pause_preview_btn.configure(text='Play' if self.preview.user_paused else 'Pause')
            if self.preview.current_frame is not None:
                self._layout_preview_controls(self.preview.display_bounds)
        else:
            self.preview.stop()
            self.preview.place_forget()
            self.preview_bar.place_forget()
            self.video_prompt.place(relx=.5, rely=.5, anchor='center', relwidth=.94)
            self.video_label.configure(text='Select a Minecraft building video')

    def _toggle_preview(self):
        paused = self.preview.toggle_pause()
        self.pause_preview_btn.configure(text='Play' if paused else 'Pause')

    def _seek_preview(self, value):
        if self._preview_progress_updating:
            return
        self.preview.seek(float(value))

    def _update_preview_progress(self, position, duration):
        if not hasattr(self, 'preview_progress'):
            return
        if duration <= 0:
            self.preview_progress.configure(state='disabled')
            return
        self._preview_progress_updating = True
        try:
            self.preview_progress.configure(state='normal', to=1.0)
            self.preview_progress.set(max(0.0, min(1.0, position/duration)))
        finally:
            self._preview_progress_updating = False

    def _preview_controls_backdrop(self):
        return self.preview.sample_region(self.preview_bar.winfo_x(), self.preview_bar.winfo_y(),
                                          max(1, self.preview_bar.winfo_width()), max(1, self.preview_bar.winfo_height()))

    def _layout_preview_controls(self, bounds):
        if self._content != 'video' or not self.preview.winfo_ismapped():
            return
        left, top, width, height = bounds
        margin, bar_height = self._u(8), self._u(52)
        bar_width = min(max(self._u(160), round(width)-2*margin), self.preview.winfo_width()-2*margin)
        bar_width = max(1, bar_width)
        x = max(margin, min(round(left+(width-bar_width)/2), self.preview.winfo_width()-bar_width-margin))
        y = max(margin, min(round(top+height)-bar_height-margin, self.preview.winfo_height()-bar_height-margin))
        placement = (x, y, bar_width, bar_height)
        moved = placement != self._preview_bar_bounds or not self.preview_bar.winfo_ismapped()
        if moved:
            self._preview_bar_bounds = placement
            self.preview_bar.place(x=x, y=y, width=bar_width, height=bar_height)
            self.preview_bar.lift()
            self.preview_name.grid() if bar_width >= self._u(300) else self.preview_name.grid_remove()
            self.change_video_btn.configure(text='Change')
        # This frosted material is stable while playing. Reblurring and repainting
        # six native controls periodically caused visible video/frame-clock stalls.
        # Controls still redraw on geometry, hover, text and focus changes.
        if moved:
            for widget in (self.preview_bar, self.preview_bar.body, self.preview_name,
                           self.pause_preview_btn, self.change_video_btn, self.clear_video_btn):
                if widget.winfo_ismapped():
                    widget._draw_chrome() if isinstance(widget, RoundedButton) else widget._draw()

    def _preview_failed(self, message):
        self.preview.place_forget()
        self.preview_bar.place_forget()
        self.video_prompt.place(relx=.5, rely=.5, anchor='center', relwidth=.94)
        self.video_label.configure(text='Video preview unavailable')
        self.status.set('Preview unavailable: '+message)

    def generate(self):
        if self._busy:
            return
        self._cancel_confirmation()
        hints = self.hints_box.get()
        self.building_hints.set(hints)
        if not self.video.get() or not Path(self.video.get()).is_file():
            self._notice('Select a Minecraft construction video first.')
            return
        mode = self.provider.get()
        if mode == 'chatgpt' and self.model.get() not in self.model_mapping:
            self._notice('Sign in and refresh available models first.')
            return
        if mode == 'api' and (not self.api_key.get().strip() or not self.api_model.get().strip()):
            self._notice('Enter an API key and model.')
            return
        if len(hints) > 8192:
            self._notice('Building hints must be at most 8,192 characters.')
            return
        try:
            frames = int(self.frames.get())
            if not 2 <= frames <= MAX_FRAMES:
                raise ValueError()
        except (tk.TclError, ValueError):
            self._notice(f'Frame count must be between 2 and {MAX_FRAMES}.')
            return
        try:
            if not self.output.get().strip():
                raise ValueError('Choose an output ZIP path.')
            dest = Path(self.output.get()).expanduser().resolve()
            if dest.suffix.lower() != '.zip' or dest.is_dir():
                raise ValueError('Output must be a ZIP filename, not a directory.')
            if dest == Path(self.video.get()).resolve():
                raise ValueError('Output must be different from the source video.')
            snapshot = output_snapshot(dest)
        except (ValueError, OSError) as exc:
            self._notice(str(exc))
            return
        params = dict(video=self.video.get(), frames=frames, auth_mode=mode, building_hints=hints,
                      model=self.model_mapping[self.model.get()] if mode == 'chatgpt' else self.api_model.get().strip())
        if mode == 'api':
            params['api_key'] = self.api_key.get().strip()
        start = lambda: self._start_generation(params, dest, snapshot)
        if snapshot is not None:
            self._show_confirmation(f'This ZIP already exists:\n{dest}\nReplace it after generation succeeds?',
                                    'Replace ZIP and generate', start)
            self.status.set('Confirm ZIP replacement above or choose a different filename.')
        else:
            start()

    def _start_generation(self, params, dest, snapshot):
        if self._busy:
            return
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            self._temp_output = tempfile.TemporaryDirectory(prefix='.Visual2Craft-', dir=dest.parent)
            self._destination = dest
            self._destination_snapshot = snapshot
            params = dict(params, out=str(Path(self._temp_output.name)/'build.zip'))
            context = mp.get_context('spawn')
            self._process_queue = context.Queue()
            self._process = context.Process(target=_generation_worker, args=(params, self._process_queue), daemon=True)
            self._process.start()
        except Exception as exc:
            self._cleanup_generation()
            self._error(str(exc))
            return
        self._set_busy('generate')
        self._update_progress({'message': 'Sampling and filtering video frames…', 'fraction': 0.0})

    def _generation_complete(self, summary):
        self._cleanup_generation(keep_temp=True)
        self._pending_result = summary
        self._ready()
        self._commit_generation(self._destination_snapshot)

    def _offer_changed_destination(self, snapshot):
        self.generate_btn.configure(state='disabled')
        self._show_confirmation(f'The destination changed during generation:\n{self._destination}\nReplace this newer file?',
                                'Replace changed ZIP', lambda: self._commit_generation(snapshot), self._discard_result)
        self.status.set('Generation finished. Review the changed destination before saving.')

    def _discard_result(self):
        self._cleanup_generation()
        self.generate_btn.configure(state='normal')

    def _commit_generation(self, approved_snapshot):
        summary = self._pending_result
        try:
            current = output_snapshot(self._destination)
            if current is not None and current != approved_snapshot:
                self._offer_changed_destination(current)
                return
            os.replace(summary['zip'], self._destination)
            summary['zip'] = str(self._destination)
        except OSError as exc:
            self._discard_result()
            self._error(str(exc))
            return
        self._cleanup_generation()
        self.generate_btn.configure(state='normal')
        self._generated(summary)

    def _generated(self, summary):
        self._last_result = summary
        skipped = summary.get('skipped_regions', 0)
        self._show_details('Data Pack ZIP ready', f"{summary['zip']}\n\n{summary['commands']} block commands generated.\n"
                           f'{skipped} unknown/preserved regions left untouched.\n\n'
                           'Place the ZIP in world/datapacks, then run the commands below.\n'
                           'Back up your world. Undo does not restore overwritten terrain.', 'result')
        self.status.set('ZIP saved successfully. Back up your Minecraft world before building.')

    def _close(self):
        if self._busy:
            self.status.set('Cancel generation before closing.' if self._task_kind == 'generate'
                            else 'Wait for the account operation to finish before closing.')
            return
        self._cancel_sign_in()
        self._cancel_confirmation()
        self._cleanup_generation()
        self._closing = True
        if self._native_resize is not None:
            self._native_resize.close()
        self._renderer.close()
        self._timer_resolution.close()
        self._animation_clock.close()
        self.preview.stop()
        for callback in self.tk.call('after', 'info'):
            # Each rounded control owns its Tcl callback. Cancel timers here;
            # let the owning widget delete its command during destruction.
            self.tk.call('after', 'cancel', callback)
        self.destroy()


if __name__ == '__main__':
    mp.freeze_support()
    import argparse
    parser = argparse.ArgumentParser(description='Visual2Craft desktop studio')
    parser.add_argument('--video', help='Preselect a construction video')
    parser.add_argument('--out', help='Preselect the output ZIP path')
    args = parser.parse_args()
    app = App()
    if args.video:
        app.video.set(str(Path(args.video).resolve()))
        app._suggested_output = available_output(args.video, Path(app.output.get()).parent)
        app.output.set(app._suggested_output)
        app._back_to_video()
    if args.out:
        app.output.set(str(Path(args.out).resolve()))
    app.mainloop()
