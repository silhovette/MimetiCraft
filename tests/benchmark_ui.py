"""Visible Tk mainloop benchmark; no credentials, network, or generation calls.

Run from the project root: python tests/benchmark_ui.py --output outputs/ui_fps.json
Reports Tk image submissions, not physical monitor presentation/vsync.
"""
import argparse
import cProfile
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--seconds', type=float, default=8)
    parser.add_argument('--fps', type=float, default=90)
    parser.add_argument('--size', default='1200x800')
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--no-video', action='store_true')
    parser.add_argument('--no-particles', action='store_true')
    parser.add_argument('--topmost', action='store_true', help='Keep the benchmark visible to avoid occlusion throttling')
    parser.add_argument('--verify-images', action='store_true', help='Check the retained front image on every pump')
    args = parser.parse_args()
    samples = {name: [] for name in ('progress', 'video', 'particles', 'video_pump')}
    errors = []
    image_checks = {'checks': 0, 'blank_or_missing': 0, 'pixel_mismatches': 0}
    with tempfile.TemporaryDirectory(prefix='Visual2Craft-fps-') as directory:
        path = Path(directory)/'motion.avi'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), args.fps, (640, 360))
        if not writer.isOpened():
            raise RuntimeError('Benchmark video encoder unavailable')
        for index in range(round(args.fps*3)):
            frame = np.full((360, 640, 3), (45, 24, 30), dtype=np.uint8)
            x = (index*5) % 640
            cv2.rectangle(frame, (x, 60), (x+50, 300), (100, 220, 150), -1)
            cv2.putText(frame, f'{index:03d} / {args.fps:g} fps', (25, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, .8, (240, 240, 240), 2)
            writer.write(frame)
        writer.release()
        auth = SimpleNamespace(accounts=lambda: [], selected=lambda: None)
        with patch('app.ChatGPTAuth', return_value=auth):
            app = App()
        app.report_callback_exception = lambda kind, value, tb: errors.append(f'{kind.__name__}: {value}')
        if args.no_particles:
            app._particles = []
        app.state('normal')
        app.geometry(args.size)
        app.lift()
        app.focus_force()
        if args.topmost:
            app.attributes('-topmost', True)
        start = [float('inf')]

        def wrap(obj, method, channel):
            original = getattr(obj, method)
            def measured(*a, **kw):
                result = original(*a, **kw)
                now = time.perf_counter()
                if now >= start[0]:
                    samples[channel].append(now)
                return result
            setattr(obj, method, measured)

        wrap(app.progress_bar, '_position', 'progress')
        wrap(app.preview, '_position_frame', 'video')
        wrap(app.preview, '_pump', 'video_pump')
        wrap(app, '_animate_particles', 'particles')
        if args.verify_images:
            original_pump = app.preview._pump
            def checked_pump():
                original_pump()
                preview = app.preview
                if time.perf_counter() < start[0] or preview.current_frame is None:
                    return
                image_checks['checks'] += 1
                front = preview.itemcget(preview._frame, 'image')
                if not front or front not in {str(photo) for photo in preview._surface.photos}:
                    image_checks['blank_or_missing'] += 1
                    return
                x, y = preview.current_frame.width//2, preview.current_frame.height//2
                shown = tuple(preview.tk.call(front, 'get', x, y))
                if shown != preview.current_frame.getpixel((x, y)):
                    image_checks['pixel_mismatches'] += 1
            app.preview._pump = checked_pump

        def begin():
            if not args.no_video:
                app.video.set(str(path))
                app._back_to_video()
            app._set_busy('models')
            start[0] = time.perf_counter()+2  # Exclude first layout and decoder startup.
            app.after(round((args.seconds+2)*1000), finish)

        def finish():
            result = {'source_fps': args.fps, 'geometry': app.geometry(),
                      'preview_size': [app.preview.winfo_width(), app.preview.winfo_height()],
                      'seconds': args.seconds, 'callback_errors': errors,
                      'video_serial': app.preview.frame_serial,
                      'video_queue': app.preview.decoder.frames.qsize() if app.preview.decoder else 0,
                      'video_loops': app.preview.loop_count,
                      'renderer': 'tk-double-buffer',
                      'presentation_fps_limit': min(app.preview.decoder.fps, 90) if app.preview.decoder else None,
                      'retained_image_checks': image_checks if args.verify_images else None,
                      'frame_driver': 'tk-event',
                      'timer_resolution_active': app._timer_resolution.active,
                      'opencv_threads': cv2.getNumThreads(),
                      'measurement': 'Tk submissions, not monitor presentation', 'channels': {}}
            for name, timestamps in samples.items():
                intervals = np.diff(timestamps)*1000
                result['channels'][name] = {
                    'frames': len(timestamps),
                    'fps': round((len(timestamps)-1)/(timestamps[-1]-timestamps[0]), 2) if len(timestamps)>1 else 0,
                    'interval_p50_ms': round(float(np.percentile(intervals, 50)), 3) if len(intervals) else None,
                    'interval_p95_ms': round(float(np.percentile(intervals, 95)), 3) if len(intervals) else None,
                    'interval_p99_ms': round(float(np.percentile(intervals, 99)), 3) if len(intervals) else None,
                    'max_interval_ms': round(float(max(intervals)), 3) if len(intervals) else None,
                    'gaps_over_22ms': int(sum(intervals>22.23)),
                }
            dest = Path(args.output)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(result, indent=2), encoding='utf-8')
            print(json.dumps(result, indent=2), flush=True)
            app._busy = False
            app._close()

        app.after(600, begin)
        if args.profile:
            profiler = cProfile.Profile()
            profiler.enable()
            app.mainloop()
            profiler.disable()
            profiler.dump_stats(str(Path(args.output).with_suffix('.prof')))
        else:
            app.mainloop()
    if errors:
        raise SystemExit(1)
    if args.verify_images and not args.no_video:
        if (not image_checks['checks'] or image_checks['blank_or_missing']
                or image_checks['pixel_mismatches']):
            raise SystemExit('Retained video image verification failed')


if __name__ == '__main__':
    main()
