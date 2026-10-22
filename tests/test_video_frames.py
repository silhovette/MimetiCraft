"""Exercise the real decoder so missing runtime dependencies cannot pass unnoticed."""
from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
import tempfile
import unittest

import cv2
import numpy as np

from video2pack import _sample_frame_ids, _select_frame_indices, video_frames


class VideoFramesTests(unittest.TestCase):
    def test_real_mp4_sampling_and_jpeg_encoding(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'sample.mp4'
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'),
                                     10, (1600, 900))
            self.assertTrue(writer.isOpened(), 'MP4 encoder unavailable')
            try:
                for index in range(12):
                    writer.write(np.full((900, 1600, 3), index * 20, dtype=np.uint8))
            finally:
                writer.release()
            output = StringIO()
            with redirect_stdout(output):
                frames = video_frames(path, 4)
            self.assertIn('sampled 5/5 candidates, removed 1, kept 4', output.getvalue())
            self.assertEqual(len(frames), 4)
            self.assertEqual(frames[0][0], 0)
            self.assertEqual(frames[-1][0], 11)
            for _, jpeg in frames:
                decoded = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
                self.assertEqual(decoded.shape[:2], (720, 1280))

            # Oversampling must not invent duplicate frames from a short video.
            with redirect_stdout(StringIO()):
                short = video_frames(path, 20)
            self.assertEqual([index for index, _ in short], list(range(12)))

    def test_redundancy_filter_preserves_local_new_information_and_endpoints(self):
        blank = np.zeros((72, 96, 3), dtype=np.uint8)
        detail = blank.copy()
        detail[24:36, 36:48] = 255
        changed = np.full_like(blank, 120)
        signatures = [blank, blank.copy(), detail, changed, changed.copy(), changed.copy()]
        selected = _select_frame_indices(signatures, [1] * len(signatures), 4)
        self.assertEqual(selected, [0, 2, 4, 5])

    def test_identical_candidates_still_return_exact_target(self):
        blank = np.zeros((72, 96, 3), dtype=np.uint8)
        selected = _select_frame_indices([blank] * 11, [0] * 11, 10)
        self.assertEqual(len(selected), 10)
        self.assertEqual(selected[0], 0)
        self.assertEqual(selected[-1], 10)
        self.assertEqual(selected, sorted(set(selected)))

    def test_dense_sampling_has_no_duplicate_indices(self):
        for length in (1, 2, 12, 100, 1320):
            for count in (1, 2, 11, length, length + 1):
                indices = _sample_frame_ids(length, count)
                self.assertEqual(len(indices), min(count, length))
                self.assertEqual(indices, sorted(set(indices)))
                self.assertEqual(indices[-1], length - 1)
                self.assertTrue(all(0 <= i < length for i in indices))

    def test_missing_video_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RuntimeError, 'Cannot open video'):
                video_frames(Path(temp) / 'missing.mp4', 4)
