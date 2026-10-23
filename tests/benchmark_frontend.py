"""Repeatable startup/animation/resize benchmark and exact visual fingerprints.

Uses fake account storage and temporary video; no network or model requests.
Run before/after separately. UI timing measures submissions, not display vsync.
"""
import time
BOOT = time.perf_counter()
import argparse
from collections import defaultdict
import cProfile
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import traceback
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import ImageTk
import cv2
import numpy as np
from app import App
from ui_widgets import GlassLabel, RoundedButton, RoundedSurface
from ui_worker import RasterWorker
IMPORT_MS = (time.perf_counter()-BOOT)*1000


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--seconds', type=float, default=5)
    parser.add_argument('--resize-steps', type=int, default=40)
    parser.add_argument('--hold', type=float, default=0, help='Keep the completed GUI open for visual inspection')
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    measurements = defaultdict(list)
    frames = defaultdict(list)
    snapshots = {}
    errors = []
    result = {}
    phase = ['startup']
    phase_started = {}
    profile = cProfile.Profile()

    def instrument(cls, method):
        original = getattr(cls, method)
        def timed(self, *a, **kw):
            started = time.perf_counter()
            try:
                return original(self, *a, **kw)
            finally:
                measurements[f'{phase[0]}:{cls.__name__}.{method}'].append((time.perf_counter()-started)*1000)
        setattr(cls, method, timed)

    for cls, method in ((App, '_schedule_background'), (App, '_draw_background'),
                        (App, '_paint_glass'), (App, '_animate_particles'),
                        (App, '_paint_glass_step'), (RasterWorker, '_poll'),
                        (GlassLabel, '_measure'), (RoundedButton, '_draw_chrome'),
                        (RoundedSurface, '_draw')):
        instrument(cls, method)

    with tempfile.TemporaryDirectory(prefix='Visual2Craft-frontend-') as directory:
        video = Path(directory)/'motion.avi'
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 60, (640, 360))
        if not writer.isOpened():
            raise RuntimeError('Cannot create benchmark video')
        for index in range(180):
            pixels = np.full((360, 640, 3), (45, 24, 30), dtype=np.uint8)
            cv2.rectangle(pixels, ((index*5)%640, 40), ((index*5)%640+40, 310), (100, 220, 150), -1)
            writer.write(pixels)
        writer.release()
        auth = SimpleNamespace(accounts=lambda: [], selected=lambda: None)
        profile.enable()
        construction = time.perf_counter()
        with patch('app.ChatGPTAuth', return_value=auth):
            app = App()
        constructor_ms = (time.perf_counter()-construction)*1000
        def report_error(typ, value, tb):
            errors.append(f'{typ.__name__}: {value}')
            traceback.print_exception(typ, value, tb)
            app._busy = False
            app.after(10, app._close)
        app.report_callback_exception = report_error
        app.state('normal')
        app.geometry('2000x1300+30+30')
        app.output.set('outputs/benchmark.zip')

        def track(obj, method, channel):
            original = getattr(obj, method)
            def wrapped(*a, **kw):
                result = original(*a, **kw)
                frames[f'{phase[0]}:{channel}'].append(time.perf_counter())
                return result
            setattr(obj, method, wrapped)
        track(app.preview, '_position_frame', 'video')
        track(app.progress_bar, '_position', 'progress')
        track(app, '_animate_particles', 'particles')

        def heartbeat():
            frames[f'{phase[0]}:heartbeat'].append(time.perf_counter())
            app.after(8, heartbeat)

        def settled():
            return (not app._resize_busy and app._glass_after is None
                    and not getattr(app, '_render_pending', False)
                    and not getattr(app, '_presentation_pending', False)
                    and not getattr(app, '_glass_paint_queue', ()))

        def when_settled(callback):
            if settled():
                app.after(100, callback)
            else:
                app.after(15, lambda: when_settled(callback))

        def snapshot(name):
            state = {}
            for widget in app._glass_widgets:
                if not widget.winfo_exists() or not widget.winfo_ismapped():
                    continue
                photo = getattr(widget, '_photo', None)
                if photo is None:
                    continue
                image = ImageTk.getimage(photo)
                state[widget._w] = {'geometry': widget.winfo_geometry(), 'size': image.size,
                                    'pixels': hashlib.sha256(image.tobytes()).hexdigest()}
            state['scene'] = {'size': app._scene_pixels.size,
                              'pixels': hashlib.sha256(app._scene_pixels.tobytes()).hexdigest()}
            snapshots[name] = state

        def loaded():
            print('Initial paint ready', flush=True)
            nonlocal ready_ms
            ready_ms = (time.perf_counter()-construction)*1000
            phase[0] = 'visual'
            for widget in app._glass_widgets:
                if isinstance(widget, RoundedButton):
                    widget._hover = widget._focused = False
                    widget._hover_amount = 0.
                    widget._schedule_chrome()
            app.after(150, capture_chatgpt)

        def capture_chatgpt():
            print('Capture ChatGPT appearance', flush=True)
            snapshot('chatgpt')
            app.provider.set('api')
            app._provider_changed()
            app.after(150, lambda: when_settled(capture_api))

        def capture_api():
            print('Capture API appearance', flush=True)
            snapshot('api')
            app.provider.set('chatgpt')
            app._provider_changed()
            app.video.set(str(video))
            app._back_to_video()
            app._set_busy('models')
            app.after(1000, begin_animation)

        def begin_animation():
            print('Animation measurement', flush=True)
            phase[0] = 'animation'
            phase_started['animation'] = time.perf_counter()
            app.after(round(args.seconds*1000), begin_resize)

        def begin_resize():
            print('Resize measurement', flush=True)
            phase[0] = 'resize'
            phase_started['resize'] = time.perf_counter()
            resize(0)

        def resize(index):
            if index >= args.resize_steps:
                app.geometry('2000x1300+30+30')
                when_settled(finish)
                return
            t = index/max(1, args.resize_steps-1)
            fraction = math.sin(t*math.pi)**2
            app.geometry(f'{2000+round(430*fraction)}x{1300+round(140*fraction)}+30+30')
            app.after(16, lambda: resize(index+1))

        def summary(values):
            return {'count': len(values), 'total_ms': round(sum(values), 3),
                    'p50_ms': round(float(np.percentile(values, 50)), 3),
                    'p95_ms': round(float(np.percentile(values, 95)), 3),
                    'max_ms': round(max(values), 3)} if len(values) else {}

        def finish():
            nonlocal result
            ended = time.perf_counter()
            result = {'import_ms': round(IMPORT_MS, 3), 'constructor_ms': round(constructor_ms, 3),
                      'initial_ready_ms': round(ready_ms, 3), 'geometry': app.geometry(),
                      'callback_errors': errors, 'visual_fingerprints': snapshots,
                      'callbacks': {key: summary(value) for key, value in measurements.items()}, 'channels': {}}
            base = Path(__file__).resolve().parents[1]
            result['source_hashes'] = {name: hashlib.sha256((base/name).read_bytes()).hexdigest()
                for name in ('app.py', 'ui_widgets.py', 'ui_layout.py', 'ui_icons.py',
                             'ui_rendering.py', 'ui_worker.py', 'ui_resize.py', 'video_preview.py')}
            for key, timestamps in frames.items():
                if key.split(':')[0] not in ('animation', 'resize') or len(timestamps) < 2:
                    continue
                intervals = np.diff(timestamps)*1000
                result['channels'][key] = {**summary(intervals),
                    'fps': round((len(timestamps)-1)/(timestamps[-1]-timestamps[0]), 2),
                    'gaps_over_33ms': int(sum(intervals > 33.334))}
            result['resize_elapsed_ms'] = round((ended-phase_started.get('resize', ended))*1000, 3)
            native = getattr(app, '_native_resize', None)
            result['resize_presentation'] = native.statistics() if native is not None else None
            output.write_text(json.dumps(result, indent=2), encoding='utf-8')
            app._busy = False
            if args.hold:
                phase[0] = 'inspection'
                app.title('Visual2Craft - Performance check')
                app._ready()
                app.after(round(args.hold*1000), app._close)
            else:
                app._close()

        ready_ms = 0.
        app.after(8, heartbeat)
        app.after(200, lambda: when_settled(loaded))
        app.after(round((60+args.hold)*1000), lambda: (errors.append('Benchmark timeout'), finish()))
        app.mainloop()
        profile.disable()
        profile.dump_stats(str(output.with_suffix('.prof')))
        if args.hold and result and app._native_resize is not None:
            result['inspection_resize_presentation'] = app._native_resize.statistics()
            output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    result.pop('visual_fingerprints', None)
    print(json.dumps(result, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
