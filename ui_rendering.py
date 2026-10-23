"""Bounded, pixel-exact raster caches. Cached PIL images are read-only inputs."""
from collections import OrderedDict
import threading
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageFilter


class PixelCache:
    def __init__(self, budget=24*1024*1024):
        self.budget = budget
        self.bytes = 0
        self.items = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key, build):
        with self.lock:
            if key in self.items:
                value, _ = self.items[key]
                self.items.move_to_end(key)
                return value
        value = build()
        # Pillow's RGB core storage is four bytes/pixel even though RGB exports
        # three channels; count that padding conservatively in the byte budget.
        stride = 4 if value.mode in ('RGB', 'RGBA') else len(value.getbands())
        size = value.width*value.height*stride
        if size <= self.budget:
            with self.lock:
                if key in self.items:
                    return self.items[key][0]
                while self.items and self.bytes+size > self.budget:
                    _, (_, removed) = self.items.popitem(last=False)
                    self.bytes -= removed
                self.items[key] = (value, size)
                self.bytes += size
        return value


RASTERS = PixelCache()


def native_font(widget, setting):
    root = widget.winfo_toplevel()
    if not hasattr(root, '_font_metrics_cache'):
        root._font_metrics_cache = OrderedDict()
    cache = root._font_metrics_cache
    key = tuple(map(str, widget.tk.splitlist(setting)))
    if key not in cache:
        cache[key] = tkfont.Font(root=root, font=setting)
        if len(cache) > 64:
            cache.popitem(last=False)
    cache.move_to_end(key)
    return cache[key]


def round_mask(width, height, radius, factor=1, *, inset=0, alpha=255, stroke=0):
    key = ('round', width, height, radius, factor, inset, alpha, stroke)
    def build():
        mask = Image.new('L', (width*factor, height*factor), 0)
        box = (inset*factor, inset*factor, (width-inset)*factor-1, (height-inset)*factor-1)
        options = {'outline': alpha, 'width': stroke} if stroke else {'fill': alpha}
        ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius*factor, **options)
        return mask.resize((width, height), Image.Resampling.LANCZOS) if factor > 1 else mask
    return RASTERS.get(key, build)


def blurred_backdrop(backdrop, blur):
    width, height = backdrop.size
    if blur <= .2:
        return backdrop
    factor = .42 if width*height >= 150_000 else 1.0
    if factor < 1:
        small = backdrop.resize((max(1, round(width*factor)), max(1, round(height*factor))), Image.Resampling.BILINEAR)
        return small.filter(ImageFilter.GaussianBlur(max(.65, blur*factor))).resize((width, height), Image.Resampling.BILINEAR)
    return backdrop.filter(ImageFilter.GaussianBlur(blur))


def primary_gradient(width, height):
    def build():
        image = Image.new('RGB', (width, height))
        pen = ImageDraw.Draw(image)
        start, end = (174, 143, 247), (183, 159, 240)
        for x in range(width):
            mix = x/max(1, width-1)
            pen.line((x, 0, x, height), fill=tuple(round(a+(b-a)*mix) for a, b in zip(start, end)))
        return image
    return RASTERS.get(('primary-gradient', width, height), build)
