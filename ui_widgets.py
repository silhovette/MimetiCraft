"""Rounded presentation surfaces around native Tk controls; no extra runtime."""
import tkinter as tk
from functools import lru_cache
from pathlib import Path
import os
import math
import time
from tkinter import font as tkfont, ttk

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat, ImageTk
from ui_icons import dashed_round_outline, paint_icon
from ui_motion import mix_color
from ui_timing import FrameTimer
from ui_rendering import blurred_backdrop, native_font, primary_gradient, round_mask

BG = '#0E131D'
CARD = '#101724'
SURFACE = '#1D2534'
FIELD = '#1A2230'
BORDER = '#48546F'
WHITE = '#F1F4FA'
MUTED = '#A2AFC2'
ACCENT = '#B29AF7'


@lru_cache(maxsize=48)
def label_font(size, bold, cjk):
    fonts = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'
    name = ('msyhbd.ttc' if bold else 'msyh.ttc') if cjk else ('segoeuib.ttf' if bold else 'segoeui.ttf')
    try:
        return ImageFont.truetype(str(fonts/name), size)
    except OSError:
        return ImageFont.truetype('DejaVuSans.ttf', size)


def register_glass(widget):
    root = widget.winfo_toplevel()
    if hasattr(root, '_glass_widgets'):
        root._glass_widgets.append(widget)


def parent_pixels(widget):
    """Sample the closest material layer at the widget's actual screen position."""
    pixels, box = parent_material(widget)
    return pixels.crop(box)


def parent_material(widget):
    """Resolve an immutable parent image and crop geometry without copying it."""
    parent = widget.master
    while parent is not None:
        pixels = getattr(parent, '_glass_pixels', None) if getattr(parent, '_glass_layer', False) else None
        if pixels is None and parent is widget.winfo_toplevel():
            pixels = getattr(parent, '_scene_pixels', None)
        if pixels is not None:
            x = widget.winfo_rootx()-parent.winfo_rootx()
            y = widget.winfo_rooty()-parent.winfo_rooty()
            return pixels, (x, y, x+max(1, widget.winfo_width()), y+max(1, widget.winfo_height()))
        parent = parent.master
    size = (max(1, widget.winfo_width()), max(1, widget.winfo_height()))
    return Image.new('RGB', size, BG), (0, 0, *size)


def acrylic_pixels(backdrop, fill, radius, outline, opacity=.46, blur=5, stroke=1,
                   border_alpha=.15, *, frost=None):
    """Render translucent glass cheaply; only the small blurred layer is scaled.

    The previous 2x antialias mask + full-size Gaussian blur incurred hundreds of
    milliseconds per large pane. This implementation maintains a full-resolution
    scene and outline, but blurs a downsampled copy when the pane is large.
    """
    width, height = backdrop.size
    radius = max(0, min(round(radius), width//2, height//2))
    if width < 2 or height < 2:
        return backdrop.convert('RGB')
    backdrop = backdrop.convert('RGB')
    if frost is None:
        frost = blurred_backdrop(backdrop, blur)
    # The upper-edge highlight is intentionally restrained and merged with tint.
    tint = Image.new('RGB', (width, height), fill)
    material = Image.blend(frost, tint, max(0, min(1, opacity)))
    mask = round_mask(width, height, radius, 2 if width*height < 140_000 else 1)
    result = backdrop.copy()
    result.paste(material, (0, 0), mask)
    if outline and border_alpha > 0:
        # Composite a faint stroke without drawing a hard, fully opaque frame.
        edge_mask = round_mask(width, height, radius, alpha=round(255*border_alpha), stroke=max(1, round(stroke)))
        result.paste(Image.new('RGB', (width, height), outline), (0, 0), edge_mask)
    return result


def resizing(widget):
    """Freeze expensive glass compositing until a size-changing drag settles."""
    root = widget.winfo_toplevel()
    return getattr(root, '_resize_busy', False) and not getattr(root, '_allow_glass_paint', False)


class GlassFrame(tk.Frame):
    """Layout container whose background continues the parent acrylic material."""
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.canvas = tk.Canvas(self, bg=kwargs.get('bg', CARD), highlightthickness=0, bd=0)
        self.canvas.place(relwidth=1, relheight=1, bordermode='outside')
        self.canvas._particle_target = True
        self._item = self.canvas.create_image(0, 0, anchor='nw')
        self._draw_id = None
        self._destroying = False
        self.bind('<Configure>', self._schedule, add='+')
        register_glass(self)

    def _schedule(self, _event=None):
        if self._destroying or getattr(self.winfo_toplevel(), '_closing', False) or resizing(self):
            return
        if self._draw_id:
            self.after_cancel(self._draw_id)
        self._draw_id = self.after(40, self._draw)

    def _draw(self):
        self._draw_id = None
        if resizing(self) or not self.winfo_ismapped():
            return
        source, box = parent_material(self)
        key = (id(source), box)
        if key == getattr(self, '_paint_key', None):
            return
        self._paint_source = source
        parent = self.master
        if box == (0, 0, *source.size):
            while parent is not None:
                if getattr(parent, '_glass_pixels', None) is source and hasattr(parent, '_photo'):
                    self._photo = parent._photo
                    break
                parent = parent.master
            else:
                self._photo = ImageTk.PhotoImage(source, master=self)
        else:
            self._photo = ImageTk.PhotoImage(source.crop(box), master=self)
        self.canvas.itemconfigure(self._item, image=self._photo)
        self._paint_key = key

    def destroy(self):
        self._destroying = True
        if self._draw_id:
            self.after_cancel(self._draw_id)
        super().destroy()


class GlassLabel(tk.Canvas):
    """Composite text and backdrop together to avoid Windows canvas repaint gaps."""
    def __init__(self, master, text='', textvariable=None, font=None, fg=WHITE, bg=CARD,
                 anchor='w', justify='left', width=None, wraplength=0, **kwargs):
        self._label_options = dict(text=text, textvariable=textvariable, font=font, fg=fg,
                                   anchor=anchor, justify=justify, wraplength=wraplength)
        self._fixed_width = width is not None
        super().__init__(master, bg=bg, bd=0, highlightthickness=0, width=width or 1, height=1, **kwargs)
        self._image_item = self.create_image(0, 0, anchor='nw')
        self._measure_key = None
        self._draw_id = None
        self._destroying = False
        self._trace = textvariable.trace_add('write', lambda *_: self._schedule()) if textvariable is not None else None
        self.bind('<Configure>', self._schedule, add='+')
        register_glass(self)
        self._measure()

    def _text(self):
        variable = self._label_options['textvariable']
        return variable.get() if variable is not None else self._label_options['text']

    def _measure(self):
        options = self._label_options
        text = self._text()
        key = (text, str(options['font']), options['wraplength'])
        if key == self._measure_key:
            return
        self._measure_key = key
        native = native_font(self, options['font'])
        self._line_height = native.metrics('linespace')
        setting = options['font']
        size = abs(int(setting[1])) if isinstance(setting, tuple) and int(setting[1]) < 0 else round(self._line_height*.75)
        self._font_image = label_font(size, native.actual('weight') == 'bold', any(ord(c) >= 0x2E80 for c in text))
        self._lines = []
        limit = options['wraplength']
        for paragraph in text.split('\n'):
            if not limit or (paragraph.isascii() and self._font_image.getlength(paragraph) <= limit):
                self._lines.append(paragraph)
                continue
            line = ''
            for char in paragraph:
                if limit and line and self._font_image.getlength(line+char) > limit:
                    split = line.rfind(' ')
                    if split > 0:
                        self._lines.append(line[:split])
                        line = line[split+1:]+char
                    else:
                        self._lines.append(line)
                        line = char
                else:
                    line += char
            self._lines.append(line)
        dimensions = dict(height=max(1, len(self._lines)*self._line_height+4))
        if not self._fixed_width:
            dimensions['width'] = max(1, round(max(self._font_image.getlength(line) for line in self._lines))+4)
        super().configure(**dimensions)

    def _schedule(self, _event=None):
        if self._destroying or getattr(self.winfo_toplevel(), '_closing', False) or resizing(self):
            return
        if self._draw_id:
            self.after_cancel(self._draw_id)
        self._draw_id = self.after_idle(self._draw)

    def _draw(self):
        self._draw_id = None
        if resizing(self) or not self.winfo_ismapped():
            return
        self._measure()
        source, box = parent_material(self)
        key = (id(source), box, self._measure_key, self._label_options['fg'], self._label_options['anchor'])
        if key == getattr(self, '_paint_key', None):
            return
        self._paint_source = source
        image = source.crop(box)
        draw = ImageDraw.Draw(image)
        y = (self.winfo_height()-len(self._lines)*self._line_height)/2
        for line in self._lines:
            x = ((self.winfo_width()-self._font_image.getlength(line))/2
                 if self._label_options['anchor'] == 'center' else 2)
            draw.text((x, y), line, font=self._font_image, fill=self._label_options['fg'])
            y += self._line_height
        self._photo = ImageTk.PhotoImage(image, master=self)
        self.itemconfigure(self._image_item, image=self._photo)
        self._paint_key = key

    def configure(self, cnf=None, **kwargs):
        if not hasattr(self, '_image_item'):
            return super().configure(cnf, **kwargs)
        label_changes = {}
        for key in list(kwargs):
            if key in self._label_options:
                value = kwargs.pop(key)
                if value != self._label_options[key]:
                    label_changes[key] = value
        self._label_options.update(label_changes)
        result = super().configure(cnf, **kwargs) if cnf or kwargs else None
        if label_changes:
            self._measure()
            self._schedule()
        return result

    config = configure

    def cget(self, key):
        if hasattr(self, '_label_options') and key in self._label_options:
            return self._text() if key == 'text' else self._label_options[key]
        return super().cget(key)

    def destroy(self):
        self._destroying = True
        if self._draw_id:
            self.after_cancel(self._draw_id)
        if self._trace:
            self._label_options['textvariable'].trace_remove('write', self._trace)
        super().destroy()


class IconView(tk.Canvas):
    def __init__(self, master, name, color='#BCA5FA', glow=False):
        super().__init__(master, bg=master.cget('bg'), highlightthickness=0, bd=0, width=1, height=1)
        self.name, self.color, self.glow = name, color, glow
        self._item = self.create_image(0, 0, anchor='nw')
        self._draw_id = None
        self._destroying = False
        self.bind('<Configure>', self._schedule)
        register_glass(self)

    def _schedule(self, _event=None):
        if not self._destroying and not getattr(self.winfo_toplevel(), '_closing', False) and not resizing(self):
            if self._draw_id:
                self.after_cancel(self._draw_id)
            self._draw_id = self.after_idle(self._draw)

    def _draw(self):
        self._draw_id = None
        if resizing(self) or not self.winfo_ismapped():
            return
        source, box = parent_material(self)
        key = (id(source), box, self.name, self.color, self.glow)
        if key == getattr(self, '_paint_key', None):
            return
        self._paint_source = source
        pixels = source.crop(box)
        size = min(pixels.size)
        paint_icon(pixels, self.name, ((pixels.width-size)/2, (pixels.height-size)/2, size), self.color, glow=self.glow)
        self._photo = ImageTk.PhotoImage(pixels, master=self)
        self.itemconfigure(self._item, image=self._photo)
        self._paint_key = key

    def destroy(self):
        self._destroying = True
        if self._draw_id:
            self.after_cancel(self._draw_id)
        super().destroy()


class RoundedSurface(tk.Frame):
    """An expanding card with an inset native content frame."""
    def __init__(self, master, *, fill=CARD, background=None, radius=18, padding=18,
                 outline=BORDER, shadow=False, scale=1):
        background = background or master.cget('bg')
        super().__init__(master, bg=background, bd=0, highlightthickness=0)
        self.fill, self.background, self.outline = fill, background, outline
        self.radius, self.shadow = round(radius*scale), shadow
        self.canvas = tk.Canvas(self, bg=background, bd=0, highlightthickness=0)
        self.canvas.place(relwidth=1, relheight=1)
        inset = round(padding*scale)
        self._glass_layer = True
        self.opacity = .45 if fill == CARD else .6
        self.blur = 4*scale
        self.dashed = False
        self._material_scale = scale
        self.canvas._particle_target = True
        self.body = GlassFrame(self, bg=fill, bd=0, highlightthickness=0)
        self.body.place(x=inset, y=inset, relwidth=1, relheight=1,
                        width=-2*inset, height=-2*inset)
        self._item = self.canvas.create_image(0, 0, anchor='nw')
        self._draw_id = None
        self._destroying = False
        self.backdrop_provider = None
        self.bind('<Configure>', self._schedule)
        # Keep a stable requested size; wrapped content does not resize its card.
        self.configure(width=round(200*scale), height=round(100*scale))
        register_glass(self)

    def _schedule(self, _event=None):
        if self._destroying or getattr(self.winfo_toplevel(), '_closing', False) or resizing(self):
            return
        if self._draw_id:
            self.after_cancel(self._draw_id)
        self._draw_id = self.after(20, self._draw)

    def _draw(self):
        self._draw_id = None
        if resizing(self) or not self.winfo_ismapped():
            return
        root = self.winfo_toplevel()
        if self.backdrop_provider:
            backdrop = self.backdrop_provider()
            key = getattr(self, 'backdrop_key', lambda: None)()
        else:
            material, box = parent_material(self)
            self._material_source = material
            key = (id(material), box)
            backdrop = None
        signature = (key, self.winfo_width(), self.winfo_height(), self.fill, self.radius,
                     self.outline, self.opacity, self.blur, self._material_scale, self.dashed)
        if key is not None and signature == getattr(self, '_raster_signature', None):
            self._raster_pending = False
            return
        if backdrop is None:
            backdrop = material.crop(box)
        fill, radius, outline, opacity, blur, scale, dashed = (self.fill, self.radius, self.outline,
            self.opacity, self.blur, self._material_scale, self.dashed)
        def render():
            pixels = acrylic_pixels(backdrop, fill, radius, outline, opacity, blur, max(1, scale), border_alpha=.12)
            if dashed:
                strokes = pixels.copy()
                dashed_round_outline(strokes, round(14*scale), round(12*scale), '#61718A', 5*scale, 4*scale, scale)
                pixels = Image.blend(pixels, strokes, .24)
            return pixels
        def apply(pixels):
            self._raster_pending = False
            if self._destroying or not self.winfo_exists():
                return
            self._raster_signature = signature
            self._glass_pixels = pixels
            self._photo = ImageTk.PhotoImage(pixels, master=self)
            self.canvas.itemconfigure(self._item, image=self._photo)
            if hasattr(root, '_surface_painted'):
                root._surface_painted(self)
        if hasattr(root, '_renderer') and backdrop.width*backdrop.height >= 140_000:
            self._raster_pending = True
            root._renderer.submit(self, render, apply)
        else:
            apply(render())

    def destroy(self):
        self._destroying = True
        if self._draw_id:
            self.after_cancel(self._draw_id)
        super().destroy()


class RoundedButton(tk.Button):
    """Native button with rounded image chrome, hover and keyboard-focus states."""
    def __init__(self, master, text, command, *, font, scale=1, primary=False,
                 danger=False, selected=False, compact=False, micro=False):
        self._scale = scale
        self._compact = compact
        self._micro = micro
        self._hover_amount = 0.
        self._hover_tick = None
        self._copy_reset = None
        self._copied = False
        self.icon = None
        self.command_style = False
        self.variant = None
        self._primary, self._danger, self._selected = primary, danger, selected
        self._hover = self._focused = False
        self._chrome_ready = False
        self._chrome_id = None
        self._layout_size = {}
        self._configured_options = {}
        self._requested_key = None
        background = master.cget('bg')
        # An image makes width/height device pixels rather than text units.
        self._photo = tk.PhotoImage(master=master, width=1, height=1)
        super().__init__(master, text=text, command=command, font=font, image=self._photo,
                         compound='none', bg=background, activebackground=background,
                         fg=BG if primary else WHITE, activeforeground=BG if primary else WHITE,
                         disabledforeground='#718097', bd=0, highlightthickness=0,
                         relief='flat', overrelief='flat', default='disabled', padx=0, pady=0, cursor='hand2', takefocus=True)
        self._chrome_ready = True
        self._set_requested_size()
        self.bind('<Configure>', self._schedule_chrome)
        self.bind('<Enter>', lambda _: self._interaction(hover=True))
        self.bind('<Leave>', lambda _: self._interaction(hover=False))
        self.bind('<FocusIn>', lambda _: self._interaction(focused=True))
        self.bind('<FocusOut>', lambda _: self._interaction(focused=False))
        self.bind('<Return>', lambda _: self.invoke())
        self._schedule_chrome()
        register_glass(self)

    def _set_requested_size(self):
        key = (self.cget('text'), str(self.cget('font')), self._scale,
               tuple(self._layout_size.items()), self._micro, self._compact, self._primary)
        if key == self._requested_key:
            return
        self._requested_key = key
        font = native_font(self, self.cget('font'))
        dimensions = dict(width=font.measure(self.cget('text'))+round((12 if self._micro else 18 if self._compact else 28)*self._scale),
                          height=max(round((24 if self._micro else 26 if self._compact else 44 if self._primary else 36)*self._scale),
                                     font.metrics('linespace')+round((5 if self._micro else 6 if self._compact else 12)*self._scale)))
        # Text changes must not replace dimensions assigned by the layout.
        dimensions.update(self._layout_size)
        super().configure(**dimensions)

    def configure(self, cnf=None, **kwargs):
        if cnf is None and kwargs:
            kwargs = {key: value for key, value in kwargs.items()
                      if key not in self._configured_options or self._configured_options[key] != value}
            if not kwargs:
                return None
        result = super().configure(cnf, **kwargs)
        options = {**(cnf if isinstance(cnf, dict) else {}), **kwargs}
        self._configured_options.update(options)
        self._layout_size.update({k: options[k] for k in ('width', 'height') if k in options})
        if self._chrome_ready and (cnf or kwargs) and (cnf or any(k != 'command' for k in kwargs)):
            if 'text' in kwargs or 'font' in kwargs:
                placement = self.place_info() if self.winfo_manager() == 'place' else {}
                if not placement.get('width') or not placement.get('height'):
                    self._set_requested_size()
            self._schedule_chrome()
        return result

    config = configure

    def set_selected(self, selected):
        self._selected = selected
        self._schedule_chrome()

    def _interaction(self, hover=None, focused=None):
        if hover is not None and self._hover != hover:
            self._hover = hover
            if self._hover_tick is None:
                self._hover_timer = FrameTimer(self)
                self._hover_last = time.monotonic()
                self._animate_hover()
        if focused is not None and self._focused != focused:
            self._focused = focused
            self._schedule_chrome()

    def _animate_hover(self):
        self._hover_tick = None
        if not self.winfo_exists() or getattr(self.winfo_toplevel(), '_closing', False):
            return
        target = 1. if self._hover and str(self.cget('state')) != 'disabled' else 0.
        delta = target-self._hover_amount
        now = time.monotonic()
        elapsed = now-self._hover_last
        self._hover_last = now
        if abs(delta) < .005:
            self._hover_amount = target
        else:
            self._hover_amount += delta*(1-math.exp(-elapsed/.044))
        self._schedule_chrome()
        if self._hover_amount != target:
            self._hover_tick = self._hover_timer.schedule(self._animate_hover)

    def flash_copied(self):
        if self._copy_reset is not None:
            self.after_cancel(self._copy_reset)
        self._copied = True
        self._schedule_chrome()
        def reset():
            self._copy_reset = None
            self._copied = False
            self._schedule_chrome()
        self._copy_reset = self.after(1400, reset)

    def _schedule_chrome(self, _event=None):
        if not self._chrome_ready or getattr(self.winfo_toplevel(), '_closing', False) or resizing(self):
            return
        if self._chrome_id:
            self.after_cancel(self._chrome_id)
        self._chrome_id = self.after_idle(self._draw_chrome)

    def _draw_chrome(self):
        self._chrome_id = None
        if resizing(self) or not self.winfo_ismapped():
            return
        if self.winfo_width() < 4 or self.winfo_height() < 4:
            return
        disabled = str(self.cget('state')) == 'disabled'
        hover = self._hover_amount if not disabled else 0.
        if disabled:
            fill = '#253042'
        elif self._danger:
            fill = mix_color('#92384F', '#B34B64', hover)
        elif self._primary:
            fill = mix_color(ACCENT, '#DED5FF', hover)
        else:
            fill = '#675087' if self._selected else mix_color(SURFACE, '#2B374A', hover)
        edge = '#D9C6FF' if self._focused or self._selected else '#9F8AE2' if self._primary else BORDER
        material, box = parent_material(self)
        material_key = (id(material), box, 3*self._scale)
        setting, text = self.cget('font'), self.cget('text')
        paint_key = (material_key, fill, edge, disabled, self._primary, self._selected, self._focused,
                     self._compact, self._micro, self._copied, self.icon, self.command_style, str(setting), text)
        if paint_key == getattr(self, '_paint_key', None):
            return
        if material_key != getattr(self, '_frost_key', None):
            self._frost_source = material
            self._frost_key = material_key
            self._backdrop_pixels = material.crop(box)
            self._frost_pixels = blurred_backdrop(self._backdrop_pixels, 3*self._scale)
        pixels = acrylic_pixels(self._backdrop_pixels, fill, round((7 if self._compact else 10)*self._scale), edge,
                                .87 if self._primary else .7, 3*self._scale, max(1, self._scale),
                                border_alpha=.52 if self._focused else .34 if self._selected else .26 if self._primary else .10,
                                frost=self._frost_pixels)
        width, height = pixels.size
        if self._primary and not disabled:
            gradient = primary_gradient(width, height)
            mask = round_mask(width, height, round(11*self._scale), inset=1, alpha=245)
            pixels.paste(gradient, mask=mask)
        if self._selected and not disabled:
            pen = ImageDraw.Draw(pixels)
            pen.rounded_rectangle((1, 1, width-2, height-2), radius=round(10*self._scale),
                                  outline='#8772B1', width=max(1, round(1*self._scale)))
        native = native_font(self, setting)
        specification = self.tk.splitlist(str(setting))
        requested = int(specification[1]) if len(specification) > 1 else int(native.cget('size'))
        size = abs(requested) if requested < 0 else round(requested*self.winfo_fpixels('1p'))
        font = label_font(size, native.actual('weight') == 'bold', False)
        color = '#101025' if self._primary else '#778397' if disabled else WHITE
        tw = font.getlength(text)
        icon_size = round((17 if self._micro else 24 if self._primary else 23)*self._scale)
        if self.command_style:
            paint_icon(pixels, 'command', (round(22*self._scale), (height-icon_size)/2, icon_size), '#C5BEF6')
            paint_icon(pixels, 'check' if self._copied else 'copy', (width-round(42*self._scale), (height-icon_size)/2, icon_size), '#A4E2C1' if self._copied else '#C5BEF6')
            tx = round(64*self._scale)
        elif self.icon:
            gap = round(15*self._scale)
            start = (width-tw-icon_size-gap)/2
            paint_icon(pixels, self.icon, (start, (height-icon_size)/2, icon_size), color if self._primary else '#C1B5F6')
            tx = start+icon_size+gap
        else:
            tx = (width-tw)/2
        ImageDraw.Draw(pixels).text((tx, height/2), text, font=font, fill=color, anchor='lm')
        self._photo = ImageTk.PhotoImage(pixels, master=self)
        # Avoid scheduling another redraw for this internal image update.
        super().configure(image=self._photo)
        self._paint_key = paint_key

    def destroy(self):
        self._chrome_ready = False
        if self._chrome_id:
            self.after_cancel(self._chrome_id)
        if self._hover_tick:
            self._hover_timer.cancel(self._hover_tick)
        if self._copy_reset:
            self.after_cancel(self._copy_reset)
        super().destroy()


class RoundedField(tk.Frame):
    """Rounded well; editing, selection and popdowns remain native Tk widgets."""
    def __init__(self, master, kind='entry', *, scale=1, **options):
        super().__init__(master, bg=master.cget('bg'), bd=0, highlightthickness=0)
        self._scale, self._focused = scale, False
        self._glass_layer = True
        self.kind = kind
        self._minimum = options.get('from_', 2)
        self._maximum = options.get('to', 1200)
        self.canvas = tk.Canvas(self, bg=self.cget('bg'), highlightthickness=0, bd=0)
        self.canvas.place(relwidth=1, relheight=1)
        self._item = self.canvas.create_image(0, 0, anchor='nw')
        if kind == 'combo':
            self.control = ttk.Combobox(self, **options)
            self.control.configure(foreground=WHITE, background=FIELD)
        else:
            cls = {'entry': tk.Entry, 'text': tk.Text, 'spin': tk.Entry}[kind]
            options.pop('from_', None)
            options.pop('to', None)
            options.update(bg=FIELD, fg=WHITE, insertbackground=WHITE, relief='flat',
                           bd=0, highlightthickness=0)
            if kind in ('entry', 'spin'):
                options.update(disabledbackground=FIELD, disabledforeground=MUTED)
            self.control = cls(self, **options)
        self.control.pack(fill='both', expand=True, padx=(round(14*scale), round((40 if kind in ('combo', 'spin') else 14)*scale)),
                          pady=round(7*scale))
        self._draw_id = None
        self._destroying = False
        super().bind('<Configure>', self._schedule)
        self.control.bind('<FocusIn>', lambda _: self._focus(True), add='+')
        self.control.bind('<FocusOut>', lambda _: self._focus(False), add='+')
        if kind == 'spin':
            self.control.bind('<Up>', lambda _: self._step(1))
            self.control.bind('<Down>', lambda _: self._step(-1))
        self.canvas.bind('<Button-1>', self._chrome_click)
        register_glass(self)

    def _step(self, delta):
        if str(self.control.cget('state')) != 'disabled':
            try:
                value = int(self.control.get())
            except ValueError:
                value = self._minimum
            value = max(self._minimum, min(self._maximum, value+delta))
            self.control.delete(0, 'end')
            self.control.insert(0, str(value))
        return 'break'

    def _chrome_click(self, event):
        if str(self.control.cget('state')) == 'disabled':
            return
        if self.kind == 'combo':
            self.control.focus_set()
            self.tk.call('ttk::combobox::Post', str(self.control))
        elif self.kind == 'spin':
            if event.x > self.winfo_width()-round(36*self._scale):
                self._step(1 if event.y < self.winfo_height()/2 else -1)
            else:
                self.control.focus_set()

    def _focus(self, focused):
        self._focused = focused
        self._schedule()

    def _schedule(self, _event=None):
        if self._destroying or getattr(self.winfo_toplevel(), '_closing', False) or resizing(self):
            return
        if self._draw_id:
            self.after_cancel(self._draw_id)
        self._draw_id = self.after_idle(self._draw)

    def _draw(self):
        self._draw_id = None
        if resizing(self) or not self.winfo_ismapped():
            return
        if self.winfo_width() < 4 or self.winfo_height() < 4:
            return
        source, box = parent_material(self)
        key = (id(source), box, self._scale, self._focused, self.kind)
        if key == getattr(self, '_paint_key', None):
            return
        self._paint_source = source
        self._glass_pixels = acrylic_pixels(source.crop(box), FIELD, round(10*self._scale),
                                             ACCENT if self._focused else BORDER, .82, 4*self._scale, max(1, self._scale),
                                             border_alpha=.48 if self._focused else .11)
        width, height = self._glass_pixels.size
        if self.kind == 'combo':
            paint_icon(self._glass_pixels, 'chevron', (width-round(36*self._scale), (height-round(20*self._scale))/2,
                                                       round(20*self._scale)), '#C8C1F1')
        elif self.kind == 'spin':
            pen = ImageDraw.Draw(self._glass_pixels)
            x0, y0 = width-round(32*self._scale), round(7*self._scale)
            pen.rounded_rectangle((x0, y0, width-round(8*self._scale), height-y0), radius=round(5*self._scale), outline='#354054')
            paint_icon(self._glass_pixels, 'up', (x0+round(4*self._scale), y0, round(16*self._scale)), '#C8C1F1')
            paint_icon(self._glass_pixels, 'down', (x0+round(4*self._scale), height-y0-round(16*self._scale),
                                                   round(16*self._scale)), '#C8C1F1')
        self._photo = ImageTk.PhotoImage(self._glass_pixels, master=self)
        self.canvas.itemconfigure(self._item, image=self._photo)
        # Native editors cannot have alpha backgrounds. Match their inset to the
        # local frosted material, while the complete rounded well is composited.
        color = '#%02x%02x%02x' % tuple(round(v) for v in ImageStat.Stat(self._glass_pixels).mean[:3])
        if self.kind != 'combo':
            self.control.configure(bg=color)
            if self.kind in ('entry', 'spin'):
                self.control.configure(disabledbackground=color)
        else:
            style = getattr(self, '_native_style', None)
            if style is None:
                self._native_style = style = ttk.Style(self)
            name = f'Glass{id(self)}.TCombobox'
            native_key = (color, round(14*self._scale))
            if native_key == getattr(self, '_native_style_key', None):
                self._paint_key = key
                return
            style.configure(name, fieldbackground=color, background=color, bordercolor=color,
                            lightcolor=color, darkcolor=color, foreground=WHITE, arrowcolor=MUTED,
                            arrowsize=round(14*self._scale), borderwidth=0, padding=0)
            style.map(name, fieldbackground=[('disabled', color), ('readonly', color), ('focus', color)],
                      background=[('active', color), ('readonly', color), ('focus', color)],
                      foreground=[('disabled', MUTED), ('readonly', WHITE), ('focus', WHITE)],
                      selectbackground=[('readonly', color), ('focus', color)],
                      selectforeground=[('readonly', WHITE), ('focus', WHITE)],
                      arrowcolor=[('disabled', MUTED), ('readonly', MUTED), ('active', '#DCCEFF')])
            self.control.configure(style=name, background=color, foreground=WHITE)
            if not getattr(self, '_native_layout_ready', False):
                style.layout(name, [('Combobox.field', {'sticky': 'nswe', 'children': [
                    ('Combobox.padding', {'sticky': 'nswe', 'children': [('Combobox.textarea', {'sticky': 'nswe'})]})]})])
                self._native_layout_ready = True
            self._native_style_key = native_key
        self._paint_key = key

    def configure(self, cnf=None, **kwargs):
        if not hasattr(self, 'control'):
            return super().configure(cnf, **kwargs)
        if cnf is None and 'font' in kwargs:
            old = tuple(map(str, self.tk.splitlist(self.control.cget('font'))))
            new = tuple(map(str, self.tk.splitlist(kwargs['font'])))
            if old == new:
                kwargs.pop('font')
                if not kwargs:
                    return None
        return self.control.configure(cnf, **kwargs)

    config = configure

    def cget(self, key):
        if hasattr(self, 'control') and key in self.control.keys():
            return self.control.cget(key)
        return super().cget(key)

    def __setitem__(self, key, value):
        self.control[key] = value

    def bind(self, sequence=None, func=None, add=None):
        return self.control.bind(sequence, func, add)

    def focus_set(self):
        self.control.focus_set()

    def get(self, *args):
        return self.control.get(*args)

    def delete(self, *args):
        return self.control.delete(*args)

    def insert(self, *args):
        return self.control.insert(*args)

    def destroy(self):
        self._destroying = True
        if self._draw_id:
            self.after_cancel(self._draw_id)
        super().destroy()
