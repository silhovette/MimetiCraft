"""Small Tk vector animations. No per-frame PIL filters or layout changes."""
from __future__ import annotations

import math
import time
import tkinter as tk

from PIL import Image, ImageDraw, ImageTk
from ui_timing import FrameTimer


def mix_color(a, b, fraction):
    """Interpolate a CSS-style hex color without creating a PIL image."""
    fraction = max(0., min(1., fraction))
    rgb = (round(int(a[i:i+2], 16)*(1-fraction)+int(b[i:i+2], 16)*fraction)
           for i in (1, 3, 5))
    return '#%02X%02X%02X' % tuple(rgb)


class ActivityBar(tk.Canvas):
    """Lightweight progress bar supporting both determinate and indeterminate modes.

    Determinate mode shows real left-to-right completion and gently eases toward
    each newly reported value. Indeterminate mode remains available for short
    account/network tasks whose completion fraction is unknowable.
    """
    def __init__(self, master, *, scale=1):
        super().__init__(master, height=max(8, round(10*scale)), bg=master.cget('bg'),
                         highlightthickness=0, bd=0)
        self.scale = scale
        self._running = False
        self._tick_id = None
        self._timer = FrameTimer(self)
        self._last_step = None
        self._started_at = 0.
        self._last_size = (0, 0)
        self._mode = 'indeterminate'
        self._value = 0.
        self._target = 0.
        self._track = self.create_line(0, 0, 1, 0, fill='#293247', width=max(3, round(4*scale)),
                                       capstyle='round')
        self._glow = self.create_line(0, 0, 1, 0, fill='#605485', width=max(6, round(7*scale)),
                                      capstyle='round')
        self._light = self.create_line(0, 0, 1, 0, fill='#BBA6FF', width=max(3, round(4*scale)),
                                       capstyle='round')
        self.bind('<Configure>', self._resize)
        self.itemconfigure(self._glow, state='hidden')
        self.itemconfigure(self._light, state='hidden')

    def _resize(self, event=None):
        w, h = self.winfo_width(), self.winfo_height()
        if (w, h) == self._last_size:
            return
        self._last_size = (w, h)
        inset = max(6, round(7*self.scale))
        self.coords(self._track, inset, h/2, max(inset, w-inset), h/2)
        if self._running:
            self._position(time.monotonic())

    def start(self, _interval=None, *, mode='indeterminate', value=0.0):
        self._mode = mode
        self._value = self._target = max(0., min(1., float(value)))
        if self._running:
            self._position(time.monotonic())
            return
        self._running = True
        self._last_step = time.monotonic()
        self._started_at = time.monotonic()
        self.itemconfigure(self._glow, state='normal')
        self.itemconfigure(self._light, state='normal')
        self._animate()

    def set_progress(self, value):
        value = max(0., min(1., float(value)))
        self._mode = 'determinate'
        self._target = max(self._target, value)
        if not self._running:
            self.start(mode='determinate', value=self._target)
        elif self._tick_id is None:
            self._animate()

    def _position(self, now):
        if not self._running:
            return
        width, height = self.winfo_width(), self.winfo_height()
        inset = max(6, round(7*self.scale))
        span = max(0, width-2*inset)
        if self._mode == 'determinate':
            delta = self._target-self._value
            if abs(delta) <= .002:
                self._value = self._target
            else:
                elapsed = max(0., now-(self._last_step or now))
                self._value += delta*(1-math.exp(-elapsed/0.09))
            self._last_step = now
            end = inset + span*self._value
            self.coords(self._glow, inset, height/2, max(inset, end), height/2)
            self.coords(self._light, inset, height/2, max(inset, end), height/2)
            return
        # Smooth reversible travel for indeterminate work.
        phase = (now-self._started_at)/2.1*math.tau
        center = .5-.5*math.cos(phase)
        length = .22 + .10*(.5+.5*math.cos(phase*2))
        start = inset + span*max(0, center*(1-length))
        end = inset + span*min(1, center*(1-length)+length)
        self.coords(self._glow, start, height/2, end, height/2)
        self.coords(self._light, start, height/2, end, height/2)

    def _animate(self):
        self._tick_id = None
        if not self._running:
            return
        active = self.winfo_ismapped() and self.winfo_toplevel().state() != 'iconic'
        if active:
            self._position(time.monotonic())
        if self._mode == 'determinate':
            needs_tick = not active or abs(self._target-self._value) > .002
            if needs_tick:
                self._tick_id = self._timer.schedule(self._animate) if active else self.after(120, self._animate)
        else:
            self._tick_id = self._timer.schedule(self._animate) if active else self.after(120, self._animate)

    def stop(self):
        self._running = False
        self._mode = 'indeterminate'
        self._value = self._target = 0.
        if self._tick_id is not None:
            self._timer.cancel(self._tick_id)
            self._tick_id = None
        self.itemconfigure(self._glow, state='hidden')
        self.itemconfigure(self._light, state='hidden')

    def destroy(self):
        self.stop()
        super().destroy()


class CopyToast(tk.Canvas):
    """Non-modal, self-dismissing copy feedback; fades without window opacity hacks."""
    def __init__(self, master, *, scale=1):
        self.scale = scale
        width, height = round(226*scale), round(43*scale)
        super().__init__(master, width=width, height=height, bg='#131A29',
                         highlightthickness=0, bd=0, takefocus=False)
        self._tick_id = None
        self._timer = FrameTimer(self)
        self._started_at = 0.
        self._background = self.create_image(0, 0, anchor='nw')
        self._underlay = None
        self._toast_photo = None
        self._last_fade = -1

        self._check = self.create_text(round(19*scale), height/2, text='✓', fill='#B7E3C9',
                                       font=('Segoe UI', -round(17*scale), 'bold'), anchor='w')
        self._message = self.create_text(round(46*scale), height/2, text='Copied to clipboard',
                                         fill='#E7E9F5', font=('Segoe UI', -round(13*scale)), anchor='w')
        self.place_forget()

    def show(self, message='Copied to clipboard'):
        if self._tick_id is not None:
            self._timer.cancel(self._tick_id)
            self._tick_id = None
        self.itemconfigure(self._message, text=message)
        self._started_at = time.monotonic()
        self._underlay = None
        self._last_fade = -1
        self._animate()

    def _animate(self):
        self._tick_id = None
        age = time.monotonic()-self._started_at
        if age >= 1.85:
            self.place_forget()
            return
        fade = min(1., age/.18, max(0., (1.85-age)/.32))
        # Float in by a few logical pixels; do not disturb any other widget.
        slide = round(6*self.scale*(1-min(1., age/.22)))
        self.place(relx=.5, y=round(22*self.scale)+slide, anchor='n')
        self.tk.call('raise', self._w)
        self._paint_background(fade)
        self.itemconfigure(self._check, fill=mix_color('#131A29', '#B7E3C9', fade))
        self.itemconfigure(self._message, fill=mix_color('#131A29', '#E7E9F5', fade))
        if age < .3 or age > 1.45:
            self._tick_id = self._timer.schedule(self._animate)
        else:
            self._tick_id = self.after(120, self._animate)

    def _paint_background(self, fade):
        # Cache what is truly beneath the toast, including the large acrylic
        # panes, so its rounded corners have no opaque rectangular backdrop.
        # This 226x43 raster is only updated during the short fade transitions.
        step = round(fade*90)
        if self._last_fade == step:
            return
        self._last_fade = step
        root = self.winfo_toplevel()
        width, height = self.winfo_width(), self.winfo_height()
        if width < 2 or height < 2:
            return
        if self._underlay is None:
            rx = self.winfo_rootx()-root.winfo_rootx()
            ry = self.winfo_rooty()-root.winfo_rooty()
            base = getattr(root, '_scene_pixels', None)
            backdrop = base.crop((rx, ry, rx+width, ry+height)) if base is not None else Image.new('RGB', (width, height), '#141B29')
            for name in ('left_card', 'right_card'):
                pane = getattr(root, name, None)
                material = getattr(pane, '_glass_pixels', None)
                if material is None:
                    continue
                px, py = pane.winfo_rootx()-root.winfo_rootx(), pane.winfo_rooty()-root.winfo_rooty()
                x0, y0 = max(rx, px), max(ry, py)
                x1, y1 = min(rx+width, px+material.width), min(ry+height, py+material.height)
                if x1 > x0 and y1 > y0:
                    backdrop.paste(material.crop((x0-px, y0-py, x1-px, y1-py)), (x0-rx, y0-ry))
            self._underlay = backdrop
        result = self._underlay.copy()
        mask = Image.new('L', (width, height), 0)
        pen = ImageDraw.Draw(mask)
        radius = max(5, round(11*self.scale))
        pen.rounded_rectangle((0, 0, width-1, height-1), radius=radius, fill=round(224*step/90))
        result.paste('#283348', (0, 0, width, height), mask)
        if step:
            stroke = Image.new('L', (width, height), 0)
            ImageDraw.Draw(stroke).rounded_rectangle((0, 0, width-1, height-1), radius=radius,
                                                     outline=round(110*step/90), width=1)
            result.paste('#7783A2', (0, 0, width, height), stroke)
        self._toast_photo = ImageTk.PhotoImage(result, master=self)
        self.itemconfigure(self._background, image=self._toast_photo)

    def destroy(self):
        if self._tick_id is not None:
            self._timer.cancel(self._tick_id)
        super().destroy()
