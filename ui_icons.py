"""Small original vector drawings rasterized at the live control resolution.

No glyphs, crops or pixels from the design-reference screenshot are used.
"""
import math
from PIL import Image, ImageDraw, ImageFilter
from ui_rendering import RASTERS


def icon_image(name, size, color='#BBA7FA', dark='#161B30', glow=False):
    factor = 3
    image = Image.new('RGBA', (size*factor, size*factor))
    draw = ImageDraw.Draw(image)
    unit = size*factor/32
    def pts(values):
        return [(x*unit, y*unit) for x, y in values]
    def line(values, width=2, fill=color):
        draw.line(pts(values), fill=fill, width=max(1, round(width*unit)), joint='curve')
    def rect(box, radius=0, fill=None, outline=color, width=2):
        draw.rounded_rectangle(tuple(v*unit for v in box), radius=radius*unit,
                               fill=fill, outline=outline, width=max(1, round(width*unit)))
    def polygon(values, fill=color):
        draw.polygon(pts(values), fill=fill)
    def ellipse(box, fill=None, outline=color, width=2):
        draw.ellipse(tuple(v*unit for v in box), fill=fill, outline=outline, width=max(1, round(width*unit)))
    if name == 'video':
        rect((2, 6, 24, 27), 4, color, None)
        polygon([(25, 12), (31, 8), (31, 25), (25, 21)])
        polygon([(11, 12), (18, 16), (11, 21)], dark)
    elif name == 'gear':
        points = []
        for index in range(32):
            angle = index*math.tau/32-math.pi/16
            radius = 15 if index % 4 in (0, 1) else 11.8
            points.append((16+math.cos(angle)*radius, 16+math.sin(angle)*radius))
        polygon(points)
        ellipse((9, 9, 23, 23), dark, None)
        ellipse((12.5, 12.5, 19.5, 19.5), color, None)
    elif name == 'terminal':
        polygon([(2, 5), (15, 5), (20, 11), (30, 11), (30, 26), (2, 26)])
        line([(8, 11), (13, 16), (8, 21)], 2.3, dark)
        line([(16, 21), (23, 21)], 2.2, dark)
    elif name == 'command':
        line([(5, 6), (14, 15), (5, 24)], 1.8)
        line([(16, 25), (28, 25)], 1.8)
    elif name == 'copy':
        rect((10, 4, 27, 23), 3, None, color, 1.6)
        rect((5, 9, 22, 28), 3, None, color, 1.6)
    elif name == 'check':
        line([(5, 16), (13, 24), (28, 8)], 2.4)
    elif name == 'folder':
        rect((4, 7, 15, 13), 2, None, color, 2)
        rect((3, 11, 28, 26), 3, dark, color, 2)
        polygon([(6, 15), (29, 15), (26, 27), (3, 27)], color)
    elif name == 'upload':
        line([(16, 22), (16, 4)], 2.2)
        line([(8, 12), (16, 4), (24, 12)], 2.2)
        line([(4, 21), (4, 28), (28, 28), (28, 21)], 2.2)
    elif name == 'refresh':
        draw.arc(tuple(v*unit for v in (4, 5, 27, 27)), 220, 545, fill=color, width=max(1, round(2*unit)))
        line([(4, 4), (5, 12), (12, 10)], 2)
        line([(21, 25), (15, 27), (17, 31)], 1.7)
    elif name == 'account':
        ellipse((10, 3, 21, 14), None, color, 1.8)
        line([(5, 27), (6, 21), (10, 18), (20, 18), (23, 21)], 1.8)
        line([(5, 27), (20, 27)], 1.8)
        line([(25, 18), (25, 28)], 1.8)
        line([(20, 23), (30, 23)], 1.8)
    elif name == 'cube':
        line([(16, 3), (29, 10), (29, 23), (16, 30), (3, 23), (3, 10), (16, 3)], 1.8)
        line([(3, 10), (16, 17), (29, 10)], 1.8)
        line([(16, 17), (16, 30)], 1.8)
    elif name == 'trash':
        line([(4, 8), (28, 8)], 1.8)
        line([(12, 7), (12, 3), (21, 3), (21, 7)], 1.8)
        rect((8, 9, 25, 29), 2, None, color, 1.8)
        line([(13, 13), (13, 25)], 1.5)
        line([(20, 13), (20, 25)], 1.5)
    elif name == 'sparkles':
        for cx, cy, radius in [(12, 14, 9), (25, 7, 5), (25, 25, 5)]:
            polygon([(cx, cy-radius), (cx+radius*.3, cy-radius*.3), (cx+radius, cy),
                     (cx+radius*.3, cy+radius*.3), (cx, cy+radius),
                     (cx-radius*.3, cy+radius*.3), (cx-radius, cy), (cx-radius*.3, cy-radius*.3)])
    elif name == 'chevron':
        line([(5, 12), (16, 23), (27, 12)], 2.3)
    elif name == 'up':
        line([(7, 21), (16, 12), (25, 21)], 2.3)
    elif name == 'down':
        line([(7, 12), (16, 21), (25, 12)], 2.3)
    image = image.resize((size, size), Image.Resampling.LANCZOS)
    if glow:
        halo = image.filter(ImageFilter.GaussianBlur(max(1, size/7)))
        halo.putalpha(halo.getchannel('A').point(lambda value: round(value*.45)))
        halo.alpha_composite(image)
        image = halo
    return image


def paint_icon(image, name, box, color='#BBA7FA', dark='#161B30', glow=False):
    x, y, size = (round(value) for value in box)
    icon = RASTERS.get(('icon', name, max(1, size), color, dark, glow),
                       lambda: icon_image(name, max(1, size), color, dark, glow))
    image.paste(icon, (x, y), icon)


def dashed_round_outline(image, inset, radius, color='#566078', dash=6, gap=4, stroke=1):
    width, height = image.size
    x0 = y0 = inset
    x1, y1 = width-1-inset, height-1-inset
    if x1 <= x0 or y1 <= y0:
        return
    radius = min(radius, (x1-x0)/2, (y1-y0)/2)
    path = []
    for cx, cy, start in [(x1-radius, y0+radius, -90), (x1-radius, y1-radius, 0),
                           (x0+radius, y1-radius, 90), (x0+radius, y0+radius, 180)]:
        for angle in range(start, start+91, 3):
            value = math.radians(angle)
            path.append((cx+radius*math.cos(value), cy+radius*math.sin(value)))
    path.append(path[0])
    draw = ImageDraw.Draw(image)
    distance = 0
    for a, b in zip(path, path[1:]):
        length = math.dist(a, b)
        steps = max(1, math.ceil(length))
        for step in range(steps):
            if (distance+step*length/steps) % (dash+gap) < dash:
                t0, t1 = step/steps, (step+1)/steps
                draw.line((a[0]+(b[0]-a[0])*t0, a[1]+(b[1]-a[1])*t0,
                           a[0]+(b[0]-a[0])*t1, a[1]+(b[1]-a[1])*t1), fill=color, width=max(1, round(stroke)))
        distance += length
