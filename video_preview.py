"""Local, silent video preview. OpenCV decoding stays off Tk's UI thread."""
import math
import queue
import threading
import time
import tkinter as tk

from PIL import Image, ImageTk

from ui_widgets import CARD, parent_pixels
from ui_timing import FrameTimer, UI_FPS
from video_surface import VideoSurface
from ui_rendering import round_mask


class PreviewDecoder:
    def __init__(self, path, *, size=(640, 360), surface=None):
        self.path = str(path)
        # A few decoded frames of lookahead absorb scheduler jitter without
        # unbounded buffering. Presentation, not decoding, owns playback time.
        self.frames = queue.Queue(maxsize=4)
        self.errors = queue.Queue(maxsize=1)
        self.stopped = threading.Event()
        self.paused = threading.Event()
        self.hidden = threading.Event()
        self.size = size
        self.fps = 25.0
        self.source_size = (0, 0)
        self.surface = surface  # Immutable (matte, mask) snapshot; never Tk objects.
        self.thread = threading.Thread(target=self._run, daemon=True, name='video-preview')
        self.thread.start()

    def _run(self):
        capture = None
        try:
            import cv2
            capture = cv2.VideoCapture(self.path)
            if not capture.isOpened():
                raise RuntimeError('This video cannot be opened for preview.')
            fps = capture.get(cv2.CAP_PROP_FPS)
            self.fps = fps if math.isfinite(fps) and .1 <= fps <= 1000 else 25.0
            playback_fps = min(self.fps, UI_FPS)
            source_step = self.fps/playback_fps
            next_source, source_cursor = 0., -1
            serial, loops = 0, 0
            while not self.stopped.is_set():
                if serial and (self.paused.is_set() or self.hidden.is_set()):
                    self.stopped.wait(.03)
                    continue
                wanted = math.floor(next_source+1e-6)
                while source_cursor+1 < wanted:
                    if not capture.grab():
                        break
                    source_cursor += 1
                ok, frame = capture.read()
                if not ok:
                    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ok, frame = capture.read()
                    loops += 1
                    next_source, source_cursor = 0., -1
                    if not ok:
                        raise RuntimeError('No readable frames are available for preview.')
                index = max(0, round(capture.get(cv2.CAP_PROP_POS_FRAMES))-1)
                source_cursor = index
                height, width = frame.shape[:2]
                self.source_size = (width, height)
                target_w, target_h = self.size
                # Center-cover the video well, rather than letterboxing its
                # footage. Cropping happens in the decoder thread (OpenCV),
                # keeping costly resizes off Tk's animation thread.
                target_w, target_h = max(1, target_w), max(1, target_h)
                cover = max(target_w/width, target_h/height)
                region_w = max(1, min(width, round(target_w/cover)))
                region_h = max(1, min(height, round(target_h/cover)))
                x0, y0 = (width-region_w)//2, (height-region_h)//2
                frame = frame[y0:y0+region_h, x0:x0+region_w]
                if frame.shape[1] != target_w or frame.shape[0] != target_h:
                    frame = cv2.resize(frame, (target_w, target_h),
                                       interpolation=cv2.INTER_AREA if cover < 1 else cv2.INTER_LINEAR)
                image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                raw = image
                surface = self.surface
                if surface is not None and surface[0].size == image.size:
                    image = surface[0].copy()
                    image.paste(raw, (0, 0), surface[1])
                serial += 1
                packet = (serial, index, loops, raw, image)
                while not self.stopped.is_set():
                    try:
                        self.frames.put(packet, timeout=.03)
                        break
                    except queue.Full:
                        continue
                next_source += source_step
        except Exception as exc:
            if not self.stopped.is_set():
                self.errors.put(str(exc))
        finally:
            if capture is not None:
                capture.release()

    def close(self):
        self.stopped.set()
        # Wake a producer waiting on a full lookahead queue immediately instead
        # of blocking the UI until its 30 ms put timeout expires.
        try:
            self.frames.get_nowait()
        except queue.Empty:
            pass
        self.thread.join(timeout=.5)


class VideoPreview(tk.Canvas):
    def __init__(self, master, *, on_error, on_frame=None):
        super().__init__(master, bg=CARD, bd=0, highlightthickness=0, takefocus=True)
        self.on_error = on_error
        self.on_frame = on_frame
        self.current_frame = None
        self.display_bounds = (0, 0, 1, 1)
        self.decoder = None
        self.path = None
        self.user_paused = False
        self._suspended = False
        self._destroying = False
        self._timer = FrameTimer(self)
        self._video_deadline = None
        self._background = self.create_image(0, 0, anchor='nw')
        self._frame = self.create_image(0, 0, anchor='center')
        self._surface = VideoSurface(self, self._frame)
        self.frame_serial = self.frame_index = self.loop_count = 0
        self._after = self.after(30, self._pump)
        self._background_after = None
        self._matte = None
        self._decoded_frame = None
        self._round_mask = None
        self._mask_size = None
        self._round_radius = 12
        self.bind('<Configure>', self._resized)

    def load(self, path):
        if self.path == str(path) and self.decoder is not None:
            self.resume()
            return
        self.stop()
        self.path = str(path)
        self.user_paused = False
        self._suspended = False
        self.frame_serial = self.frame_index = self.loop_count = 0
        self._prepare_surface()
        self.decoder = PreviewDecoder(path, size=self._mask_size, surface=(self._matte, self._round_mask))
        if self._after is not None:
            self._timer.cancel(self._after)
        self._after = self.after(10, self._pump)

    def _resized(self, _event=None):
        if self.decoder:
            self.decoder.size = (max(1, self.winfo_width()), max(1, self.winfo_height()))
        self._round_mask = None  # Rebuild only if the video's well changed size.
        self._matte = None
        self.coords(self._frame, self.winfo_width()/2, self.winfo_height()/2)
        self._position_frame()
        if self._background_after:
            self.after_cancel(self._background_after)
        self._background_after = self.after(160, self.refresh_backdrop)

    def refresh_backdrop(self):
        self._background_after = None
        if self._destroying or not self.winfo_ismapped() or getattr(self.winfo_toplevel(), '_resize_busy', False):
            return
        self._matte = parent_pixels(self)
        self._background_photo = ImageTk.PhotoImage(self._matte, master=self)
        self.itemconfigure(self._background, image=self._background_photo)
        self._prepare_surface()
        if self.user_paused and self._decoded_frame is not None:
            self.current_frame = self._rounded_frame(self._decoded_frame)
            self._present(self.current_frame)
            self.coords(self._frame, self.winfo_width()/2, self.winfo_height()/2)

    def _position_frame(self):
        if self.current_frame is not None:
            width, height = self.winfo_width(), self.winfo_height()
            self.display_bounds = (0, 0, width, height)
            if self.on_frame:
                self.on_frame(self.display_bounds)

    def sample_region(self, x, y, width, height):
        """Sample decoded pixels beneath the floating controls, never the desktop."""
        # current_frame is already corner-clipped and composited against the
        # glass underlay. Sampling its small control region needs only a crop.
        if self.current_frame is not None:
            return self.current_frame.crop((x, y, x+width, y+height))
        return (self._matte if self._matte is not None else parent_pixels(self)).crop((x, y, x+width, y+height))

    def _rounded_frame(self, frame):
        """Cover the entire well, masking only its corners against acrylic."""
        width, height = max(1, self.winfo_width()), max(1, self.winfo_height())
        if frame.size != (width, height):
            frame = frame.resize((width, height), Image.Resampling.BILINEAR)
        self._prepare_surface()
        result = self._matte.copy()
        result.paste(frame, (0, 0), self._round_mask)
        return result

    def _prepare_surface(self):
        """Publish corner compositing inputs once per geometry/backdrop change."""
        width, height = max(1, self.winfo_width()), max(1, self.winfo_height())
        if self._mask_size != (width, height) or self._round_mask is None:
            self._mask_size = (width, height)
            radius = min(round(self._round_radius*getattr(self.winfo_toplevel(), '_dpi_scale', 1)), width//2, height//2)
            factor = 2 if width*height < 1_200_000 else 1
            self._round_mask = round_mask(width, height, radius, factor)
        # Avoid re-reading the scene every video frame. The matte is invalidated
        # only when the scene/geometry actually changes.
        if self._matte is None or self._matte.size != (width, height):
            self._matte = parent_pixels(self)
        if self.decoder:
            self.decoder.surface = (self._matte, self._round_mask)

    def _present(self, frame):
        self._surface.present(frame)

    def _next_packet(self, decoder, now):
        """Present real source frames at min(source fps, 90), never invent frames."""
        interval = 1/min(decoder.fps, UI_FPS)
        tolerance = .5/UI_FPS
        if self._video_deadline is not None:
            # Bound recovery work after a UI stall; opening, switching, pause
            # and hiding reset the clock altogether.
            self._video_deadline = max(self._video_deadline, now-3*interval)
        packet = None
        for _ in range(decoder.frames.maxsize):
            if self._video_deadline is not None and self._video_deadline > now+tolerance:
                break
            try:
                packet = decoder.frames.get_nowait()
            except queue.Empty:
                break
            if self._video_deadline is None:
                self._video_deadline = now+interval
            else:
                self._video_deadline += interval
        return packet

    def _pump(self):
        self._after = None
        if self._destroying:
            return
        decoder = self.decoder
        if decoder:
            if (self._suspended or not self.winfo_ismapped() or self.winfo_toplevel().state() == 'iconic'
                    or getattr(self.winfo_toplevel(), '_resize_busy', False)):
                decoder.hidden.set()
                self._video_deadline = None
            else:
                decoder.hidden.clear()
                if self._round_mask is None or self._matte is None:
                    self._prepare_surface()
            packet = None
            if not decoder.hidden.is_set() and not self.user_paused:
                # Wall-clock media time is independent of animation tick count.
                packet = self._next_packet(decoder, time.monotonic())
            if packet is not None:
                serial, index, loops, raw, frame = packet
                if frame.size == (self.winfo_width(), self.winfo_height()) and raw is not frame:
                    self._decoded_frame = raw
                    self.current_frame = frame
                    self._present(frame)
                    self.frame_serial, self.frame_index, self.loop_count = serial, index, loops
                    self._position_frame()
            try:
                error = decoder.errors.get_nowait()
            except queue.Empty:
                pass
            else:
                self.stop()
                self.on_error(error)
        if decoder and not self.user_paused and not self._suspended and self.winfo_ismapped()                 and self.winfo_toplevel().state() != 'iconic' and not getattr(self.winfo_toplevel(), '_resize_busy', False):
            self._after = self._timer.schedule(self._pump)
            return
        elif decoder:
            delay = 120
        else:
            delay = 180
        self._after = self.after(delay, self._pump)

    def toggle_pause(self):
        self.user_paused = not self.user_paused
        self._video_deadline = None
        if self.decoder:
            self.decoder.paused.set() if self.user_paused else self.decoder.paused.clear()
        self._wake()
        return self.user_paused

    def suspend(self):
        self._suspended = True
        self._video_deadline = None
        if self.decoder:
            self.decoder.hidden.set()

    def resume(self):
        self._suspended = False
        self._video_deadline = None
        if self.decoder:
            self.decoder.hidden.clear()
        self._wake()

    def _wake(self):
        if self._after is not None:
            self._timer.cancel(self._after)
        self._after = self.after(1, self._pump)

    def stop(self):
        self._video_deadline = None
        if self.decoder:
            self.decoder.close()
        self.decoder = None
        self.path = None
        self._surface.close()
        self.current_frame = None
        self._decoded_frame = None
        self._matte = None

    def destroy(self):
        self._destroying = True
        if self._after:
            self._timer.cancel(self._after)
        if self._background_after:
            self.after_cancel(self._background_after)
        self.stop()
        super().destroy()
