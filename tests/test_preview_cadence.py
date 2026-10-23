"""Media pacing must follow source fps, not force every video to 90 fps."""
import queue
from types import SimpleNamespace
import unittest

from video_preview import VideoPreview


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        # _next_packet is deliberately independent of Tk and real timer delays.
        self.preview = object.__new__(VideoPreview)
        self.preview._video_deadline = None

    def decoder(self, fps):
        return SimpleNamespace(fps=fps, frames=queue.Queue(maxsize=4))

    def test_slow_open_does_not_start_or_age_media_clock_before_first_frame(self):
        decoder = self.decoder(25)
        for now in (10., 20., 30.):
            self.assertIsNone(self.preview._next_packet(decoder, now))
            self.assertIsNone(self.preview._video_deadline)
        decoder.fps = 30
        decoder.frames.put(0)
        self.assertEqual(self.preview._next_packet(decoder, 40.), 0)
        self.assertAlmostEqual(self.preview._video_deadline, 40+1/30)
        decoder.frames.put(1)
        self.assertIsNone(self.preview._next_packet(decoder, 40.01))
        self.assertEqual(self.preview._next_packet(decoder, 40+1/30), 1)

    def test_common_and_fractional_rates_are_preserved_with_a_90_fps_ceiling(self):
        for fps in (12, 23.976, 24, 29.97, 30, 59.94, 60, 90, 120):
            with self.subTest(fps=fps):
                self.preview._video_deadline = None
                decoder = self.decoder(fps)
                presentations = []
                serial = 0
                for tick in range(901):
                    while not decoder.frames.full():
                        decoder.frames.put(serial)
                        serial += 1
                    packet = self.preview._next_packet(decoder, 100+tick/90)
                    if packet is not None:
                        presentations.append(packet)
                self.assertLessEqual(abs(len(presentations)-(1+10*min(fps, 90))), 1)
                self.assertEqual(len(presentations), len(set(presentations)), 'Never invent repeated frames')

    def test_long_stall_has_bounded_recovery_even_if_decoder_keeps_refilling(self):
        class AlwaysReady:
            maxsize = 4
            reads = 0
            def get_nowait(self):
                self.reads += 1
                return self.reads
        frames = AlwaysReady()
        self.preview._video_deadline = 1.
        decoder = SimpleNamespace(fps=30, frames=frames)
        self.assertIsNotNone(self.preview._next_packet(decoder, 3600.))
        self.assertLessEqual(frames.reads, 4)
        self.assertGreater(self.preview._video_deadline, 3600.)


if __name__ == '__main__':
    unittest.main()
