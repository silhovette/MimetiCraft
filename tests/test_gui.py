"""Network-free regression checks for confirmation, task and output safety.

These tests create real Tk widgets, but replace account storage and model calls.
Generation checks use the offline demo scene in a real spawned worker.
"""
import multiprocessing as mp
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import zipfile
import cv2
import numpy as np

from app import App, available_output, output_snapshot
from ui_widgets import GlassLabel


class FakeAuth:
    def __init__(self):
        self.saved = [NS(client_id='one', email='first@example.test'),
                      NS(client_id='two', email='second@example.test')]
        self.active = 'one'
        self.deleted = []

    def accounts(self):
        return self.saved

    def selected(self):
        return next((a for a in self.saved if a.client_id == self.active), None)

    def select(self, cid):
        self.active = cid

    def delete_account(self, cid):
        self.deleted.append(cid)
        self.saved = [a for a in self.saved if a.client_id != cid]
        self.active = self.saved[0].client_id if self.saved else None


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.auth_patch = patch('app.ChatGPTAuth', FakeAuth)
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = App()
        self.callback_errors = []
        self.app.report_callback_exception = lambda kind, value, tb: self.callback_errors.append(f'{kind.__name__}: {value}')
        self.addCleanup(self.destroy_app)
        self.app.state('normal')
        self.app.geometry(f'{round(1200*self.app._dpi_scale)}x{round(800*self.app._dpi_scale)}')
        self.app.update()
        self.pump_until(lambda: not self.app._resize_busy)

    def destroy_app(self):
        app = self.app
        app._cleanup_generation()
        app._busy = False
        app._close()
        self.assertEqual(self.callback_errors, [], 'Tk callback errors')

    def pump_until(self, predicate, timeout=15):
        deadline = time.monotonic()+timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.update()
            time.sleep(.02)
        self.app.update()
        self.assertTrue(predicate(), self.app.status.get())

    def inputs(self):
        app = self.app
        video = self.root/'source.mp4'
        video.touch()
        app.video.set(str(video))
        app.provider.set('api')
        app._provider_changed()
        app.api_key.set('fake-key')
        app.output.set(str(self.root/'result.zip'))

    def offline_generation(self, snapshot=None):
        dest = self.root/'result.zip'
        self.app._start_generation(
            dict(scene_path=str(Path(__file__).resolve().parents[1]/'examples/demo_house.json'),
                 auth_mode='api'), dest, snapshot)
        self.pump_until(lambda: not self.app._busy)
        return dest

    def test_window_move_does_not_relayout_or_rerender(self):
        app = self.app
        width, height = app.winfo_width(), app.winfo_height()
        self.assertEqual(app._last_window_size, (width, height))
        timer = app._resize_after
        with patch('ui_layout.layout_reference_ui') as relayout:
            for _ in range(8):
                app._schedule_background(NS(widget=app, width=width, height=height))
            relayout.assert_not_called()
        self.assertEqual(app._resize_after, timer)

    @unittest.skipUnless(os.name == 'nt', 'Windows resize transaction')
    def test_resize_keeps_one_frame_until_complete_layout_is_ready(self):
        app = self.app
        self.pump_until(lambda: app._native_resize is not None and app._native_resize.ready
                        and not app._render_pending and not app._glass_paint_queue)
        native = app._native_resize
        captures, commits = native.captures, native.commits
        source_size = (app.winfo_width(), app.winfo_height())
        for width, height in ((2000, 1300), (2140, 1380), (2200, 1400)):
            app.geometry(f'{width}x{height}')
            app.update()
            self.assertTrue(app._presentation_pending)
            self.assertEqual(native.source_size, source_size)
            self.assertEqual(native.captures, captures+1)
        self.pump_until(lambda: not app._presentation_pending and not app._resize_busy)
        size = (app.winfo_width(), app.winfo_height())
        self.assertEqual(app._layout_signature[:2], size)
        self.assertEqual(app._scene_pixels.size, size)
        self.assertEqual(native.commits, commits+1)
        self.assertIsNone(native.last_error)

    def test_video_well_uses_frosted_acrylic_and_subtle_strokes(self):
        app = self.app
        self.assertGreater(app.prompt_card.opacity, .2)
        self.assertGreater(app.prompt_card.blur, 0)
        self.assertTrue(app.prompt_card.backdrop_provider)
        self.assertTrue(app.prompt_card.dashed)
        from ui_widgets import acrylic_pixels
        from PIL import Image
        sample = Image.new('RGB', (100, 70), '#5E718B')
        glass = acrylic_pixels(sample, '#101724', 12, '#FFFFFF', .4, 5, border_alpha=.1)
        self.assertNotEqual(glass.getpixel((50, 35)), sample.getpixel((50, 35)))
        self.assertNotEqual(glass.getpixel((0, 0)), glass.getpixel((50, 35)))

    def test_delete_requires_visible_confirmation_and_cancel_preserves_account(self):
        app = self.app
        app.remove_account_btn.invoke()
        app.update()
        self.assertTrue(app.confirm_yes.winfo_ismapped())
        self.assertTrue(app.confirm_no.winfo_ismapped())
        self.assertIn('first@example.test', app.confirm_label.cget('text'))
        self.assertEqual(app.auth.deleted, [])
        app.confirm_no.invoke()
        self.assertEqual(len(app.auth.saved), 2)
        app.remove_account_btn.invoke()
        app.confirm_yes.invoke()
        self.assertEqual(app.auth.deleted, ['one'])
        self.assertEqual(app.account_choice.get(), 'second@example.test')
        self.assertEqual(app.model_mapping, {})

    def test_escape_dismisses_confirmation_without_switching_layout(self):
        app = self.app
        app.remove_account_btn.invoke()
        app._close_overlay()
        self.assertIsNone(app._confirmation)
        self.assertEqual(len(app.auth.saved), 2)
        self.assertEqual(app.auth.deleted, [])
        app._focus_settings()
        self.assertTrue(app.account_frame.winfo_ismapped())

    def test_form_labels_do_not_intersect_fields_in_both_modes_and_sizes(self):
        app = self.app
        for width, height in [(960, 620), (1200, 800), (1920, 1080)]:
            app.geometry(f'{round(width*app._dpi_scale)}x{round(height*app._dpi_scale)}')
            for mode in ['api', 'chatgpt']:
                app.provider.set(mode)
                app._provider_changed()
                app.update()
                self.pump_until(lambda: not app._resize_busy and not app._presentation_pending)
                widgets = [w for w in (app.mode_frame, app.account_frame, app.api_frame,
                            *app.form_labels.values(), app.model_box, app.api_model_box,
                            app.frames_box, app.output_row, app.hints_box) if w.winfo_ismapped()]
                rows = sorted(widgets, key=lambda w: w.winfo_y())
                for upper, lower in zip(rows, rows[1:]):
                    self.assertLessEqual(upper.winfo_y()+upper.winfo_height(), lower.winfo_y(),
                                         (width, height, mode, str(upper), str(lower)))
                for widget in widgets:
                    if isinstance(widget, GlassLabel):
                        self.assertLessEqual(widget.winfo_reqwidth(), widget.winfo_width(),
                                             (width, height, mode, widget.cget('text')))
                for parent in [app.account_frame, app.api_frame]:
                    if parent.winfo_ismapped():
                        for widget in parent.winfo_children():
                            if isinstance(widget, GlassLabel):
                                self.assertLessEqual(widget.winfo_reqwidth(), widget.winfo_width())
                self.assertTrue(app.generate_btn.winfo_ismapped())
                self.assertLessEqual(app.generate_btn.winfo_rooty()+app.generate_btn.winfo_height(),
                                     app.winfo_rooty()+app.winfo_height())

    def test_refresh_shows_progress_without_generating_label_and_locks_inputs(self):
        app = self.app
        gate = threading.Event()
        app._task('Fetching available models…', lambda: (gate.wait(3), [('Test', 'test')])[1],
                  app._models_ready, kind='models')
        app.update()
        self.assertTrue(app.progress_bar.winfo_ismapped())
        self.assertEqual(app.generate_btn.cget('text'), 'Fetching models…')
        self.assertEqual(str(app.output_box.cget('state')), 'disabled')
        self.assertFalse(app.cancel_btn.winfo_ismapped())
        gate.set()
        self.pump_until(lambda: not app._busy)
        self.assertFalse(app.progress_bar.winfo_ismapped())
        self.assertEqual(app.generate_btn.cget('text'), 'Generate Data Pack')

    def test_pending_add_account_leaves_ui_usable_and_switch_ignores_late_error(self):
        app = self.app
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def signin(**kwargs):
            entered.set()
            release.wait(5)
            finished.set()
            raise RuntimeError('Old browser authorization expired')
        with patch.object(app.auth, 'sign_in', side_effect=signin, create=True), \
             patch.object(app.auth, 'models', return_value=[('Second model', 'second-model')], create=True):
            app.new_account_btn.invoke()
            self.pump_until(entered.is_set)
            attempt = app._signin_cancel
            self.assertEqual(app.account_choice.get(), '')
            self.assertEqual(set(app.account_box.control['values']), {'first@example.test', 'second@example.test'})
            self.assertEqual(str(app.account_box.cget('state')), 'readonly')
            self.assertEqual(str(app.browse_btn.cget('state')), 'normal')
            self.assertEqual(app.model_mapping, {})
            self.assertFalse(app._busy)
            self.assertTrue(app.feedback_frame.winfo_ismapped())
            app.account_choice.set('second@example.test')
            app.account_box.control.event_generate('<<ComboboxSelected>>')
            self.pump_until(lambda: not app._busy)
            self.assertTrue(attempt.is_set())
            self.assertEqual(app.auth.active, 'two')
            self.assertEqual(app.model_mapping, {'Second model [second-model]': 'second-model'})
            # A late result must not unlock another operation or replace its UI.
            app._set_busy('generate')
            release.set()
            self.pump_until(finished.is_set)
            drained = threading.Event()
            app.after(150, drained.set)
            self.pump_until(drained.is_set)
            self.assertTrue(app._busy)
            self.assertEqual(app._task_kind, 'generate')
            self.assertEqual(app.account_choice.get(), 'second@example.test')
            self.assertNotEqual(app._content, 'error')
            app._ready()

    def test_add_account_success_selects_new_account_and_fetches_its_models(self):
        app = self.app
        def signin(**kwargs):
            self.assertTrue(kwargs['add_account'])
            account = NS(client_id='three', email='third@example.test')
            app.auth.saved.append(account)
            app.auth.active = 'three'
            return account
        with patch.object(app.auth, 'sign_in', side_effect=signin, create=True), \
             patch.object(app.auth, 'models', side_effect=lambda: [(app.auth.active, 'new-model')], create=True):
            app.sign_in(add=True)
            self.pump_until(lambda: app._signin_cancel is None and not app._busy)
        self.assertEqual(app.account_choice.get(), 'third@example.test')
        self.assertEqual(app.model.get(), 'three [new-model]')
        self.assertEqual(str(app.new_account_btn.cget('state')), 'normal')

    def test_add_account_failure_restores_saved_choices_without_error_screen(self):
        app = self.app
        with patch.object(app.auth, 'sign_in', side_effect=TimeoutError('Browser timed out'), create=True):
            app.sign_in(add=True)
            self.pump_until(lambda: app._signin_cancel is None)
        self.assertEqual(app.account_choice.get(), 'first@example.test')
        self.assertIn('Browser timed out', app.status.get())
        self.assertTrue(app.feedback_frame.winfo_ismapped())
        self.assertFalse(app._busy)
        self.assertNotEqual(app._content, 'error')

    def test_api_mode_cancels_pending_signin_and_ignores_late_success(self):
        app = self.app
        with patch.object(app.auth, 'sign_in', side_effect=lambda **kw: kw['cancel_event'].wait(5), create=True):
            app.sign_in(add=True)
            attempt = app._signin_cancel
            app._select_provider('api')
            self.assertTrue(attempt.is_set())
            self.assertIsNone(app._signin_cancel)
            app._ui_queue.put(('signin', (attempt, None, None)))
            app._drain_ui_queue()
        self.assertEqual(app.provider.get(), 'api')
        self.assertFalse(app._busy)

    def test_existing_output_cannot_start_without_approval(self):
        self.inputs()
        app = self.app
        dest = Path(app.output.get())
        dest.write_bytes(b'keep-this-zip')
        with patch.object(app, '_start_generation') as start:
            app.generate()
            start.assert_not_called()
            app.confirm_no.invoke()
            start.assert_not_called()
            self.assertEqual(dest.read_bytes(), b'keep-this-zip')
            app.generate()
            app.confirm_yes.invoke()
            start.assert_called_once()

    def test_offline_worker_exports_valid_zip_and_cleans_staging(self):
        dest = self.offline_generation()
        self.assertTrue(zipfile.is_zipfile(dest))
        self.assertEqual(self.app._last_result['zip'], str(dest))
        self.assertEqual(list(self.root.iterdir()), [dest])

    def test_changed_output_requires_another_confirmation_and_cancel_preserves_it(self):
        dest = self.root/'result.zip'
        dest.write_bytes(b'old')
        snapshot = output_snapshot(dest)
        dest.write_bytes(b'newer-content')
        self.offline_generation(snapshot)
        self.assertIsNotNone(self.app._confirmation)
        self.assertEqual(dest.read_bytes(), b'newer-content')
        self.app.confirm_no.invoke()
        self.assertEqual(dest.read_bytes(), b'newer-content')
        self.assertEqual(list(self.root.iterdir()), [dest])

    def test_confirmed_changed_output_is_replaced_only_with_completed_zip(self):
        dest = self.root/'result.zip'
        dest.write_bytes(b'new-file')
        self.offline_generation(None)
        self.assertIsNotNone(self.app._confirmation)
        self.app.confirm_yes.invoke()
        self.assertTrue(zipfile.is_zipfile(dest))
        self.assertEqual(list(self.root.iterdir()), [dest])

    def test_destination_changed_again_while_confirmation_open_is_not_silently_replaced(self):
        dest = self.root/'result.zip'
        dest.write_bytes(b'first-change')
        self.offline_generation(None)
        dest.write_bytes(b'second-change-with-new-content')
        self.app.confirm_yes.invoke()
        self.assertIsNotNone(self.app._confirmation)
        self.assertEqual(dest.read_bytes(), b'second-change-with-new-content')
        self.app.confirm_no.invoke()
        self.assertEqual(list(self.root.iterdir()), [dest])

    def test_cancel_terminates_worker_and_preserves_existing_zip(self):
        app = self.app
        dest = self.root/'result.zip'
        dest.write_bytes(b'original')
        app._temp_output = tempfile.TemporaryDirectory(prefix='.Visual2Craft-', dir=self.root)
        (Path(app._temp_output.name)/'build.zip').write_bytes(b'incomplete')
        context = mp.get_context('spawn')
        app._process_queue = context.Queue()
        app._process = context.Process(target=time.sleep, args=(30,))
        app._process.start()
        process = app._process
        app._set_busy('generate')
        app.update()
        self.assertTrue(app.cancel_btn.winfo_ismapped())
        app.cancel_btn.invoke()
        self.assertFalse(app._busy)
        self.assertIsNone(app._process)
        self.assertTrue(process._closed)
        self.assertEqual(dest.read_bytes(), b'original')
        self.assertEqual(list(self.root.iterdir()), [dest])

    def test_worker_error_cleans_staging_and_leaves_output_unchanged(self):
        dest = self.root/'result.zip'
        dest.write_bytes(b'original')
        self.app._start_generation(dict(scene_path=str(self.root/'missing.json'), auth_mode='api'),
                                   dest, output_snapshot(dest))
        self.pump_until(lambda: not self.app._busy)
        self.assertEqual(self.app._content, 'error')
        self.assertEqual(dest.read_bytes(), b'original')
        self.assertEqual(list(self.root.iterdir()), [dest])

    def test_suggested_output_uses_video_name_and_avoids_existing_files(self):
        (self.root/'house.zip').touch()
        (self.root/'house_2.zip').touch()
        self.assertEqual(Path(available_output('house.mp4', self.root)).name, 'house_3.zip')

    def test_custom_frame_arrows_keep_limits_and_disabled_state(self):
        app = self.app
        app.frames.set(500)
        app.frames_box._step(1)
        self.assertEqual(app.frames.get(), 501)
        app.frames.set(1200)
        app.frames_box._step(1)
        self.assertEqual(app.frames.get(), 1200)
        app.frames.set(2)
        app.frames_box._step(-1)
        self.assertEqual(app.frames.get(), 2)
        app.frames_box.configure(state='disabled')
        app.frames_box._step(1)
        self.assertEqual(app.frames.get(), 2)

    def make_video(self, name, size=(320, 180)):
        path = self.root/name
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 12, size)
        self.assertTrue(writer.isOpened())
        try:
            for index in range(12):
                frame = np.zeros((size[1], size[0], 3), dtype=np.uint8)
                frame[:] = (index*17, 40, 220-index*12)
                cv2.rectangle(frame, (index*12, 20), (index*12+30, 80), (30, 240, 90), -1)
                writer.write(frame)
        finally:
            writer.release()
        return path

    def test_expanded_video_region_single_line_hints_and_no_footer_status(self):
        app = self.app
        self.assertGreaterEqual(app.prompt_card.winfo_width(), app.video_frame.winfo_width()*.98)
        self.assertGreaterEqual(app.prompt_card.winfo_height(), app.video_frame.winfo_height()*.98)
        self.assertEqual(app.hints_box.control.winfo_class(), 'Entry')
        self.assertFalse(any(w.winfo_class() == 'TScrollbar' for w in app.right.winfo_children()))
        self.inputs()
        app.hints_box.insert(0, 'Use a stone roof')
        with patch.object(app, '_start_generation') as start:
            app.generate()
            self.assertEqual(start.call_args.args[0]['building_hints'], 'Use a stone roof')
        app.status.set('Selected video: source.mp4')
        app.update()
        self.assertFalse(app.status_label.winfo_ismapped())
        app._notice('Choose a model.')
        app.update()
        self.assertTrue(app.status_label.winfo_ismapped())
        self.assertIs(app.feedback_frame.master, app.right)
        self.assertGreaterEqual(app.status_label.winfo_rootx(), app.right.winfo_rootx())

    def test_selected_video_plays_loops_pauses_resumes_and_switches(self):
        app = self.app
        first = self.make_video('first.mp4')
        with patch('app.filedialog.askopenfilename', return_value=str(first)):
            app.choose_video()
        self.pump_until(lambda: app.preview.frame_serial >= 3)
        old = app.preview.decoder
        self.assertTrue(app.preview.winfo_ismapped())
        self.assertFalse(app.video_prompt.winfo_ismapped())
        self.assertGreaterEqual(app.preview.winfo_height(), app.prompt_card.body.winfo_height()*.98)
        self.assertTrue(app.preview_bar.winfo_ismapped())
        bar_bottom = app.preview_bar.winfo_y()+app.preview_bar.winfo_height()
        self.assertLessEqual(bar_bottom, app.preview.winfo_height())
        self.assertLessEqual(app.preview_bar.winfo_height(), app._u(36))
        self.assertGreater(bar_bottom, app.preview.display_bounds[1])
        app.pause_preview_btn.invoke()
        self.assertTrue(old.paused.is_set())
        # Drain one frame that might already have been decoded when Pause was clicked.
        time.sleep(.12)
        app.update()
        serial = app.preview.frame_serial
        until = time.monotonic()+.2
        while time.monotonic() < until:
            app.update()
            time.sleep(.02)
        self.assertEqual(app.preview.frame_serial, serial)
        app.pause_preview_btn.invoke()
        self.pump_until(lambda: app.preview.frame_serial > serial+2 and app.preview.loop_count >= 1)
        second = self.make_video('second.mp4', (160, 120))
        with patch('app.filedialog.askopenfilename', return_value=str(second)):
            app.choose_video()
        self.pump_until(lambda: app.preview.frame_serial >= 2)
        self.assertTrue(old.stopped.is_set())
        self.assertFalse(old.thread.is_alive())
        self.assertEqual(app.preview.decoder.source_size, (160, 120))
        decoder = app.preview.decoder
        app.preview.stop()
        self.assertFalse(decoder.thread.is_alive())

    def test_activity_bar_is_smooth_truthful_and_stops_cleanly(self):
        app = self.app
        app._set_busy('models')
        app.update()
        bar = app.progress_bar
        self.assertTrue(bar.winfo_ismapped())
        self.assertEqual(len(bar.find_all()), 3)
        self.assertTrue(bar._running)
        first = bar.coords(bar._light)
        self.pump_until(lambda: bar.coords(bar._light) != first, timeout=1.5)
        app._ready()
        self.assertFalse(bar._running)
        self.assertIsNone(bar._tick_id)
        self.assertFalse(bar.winfo_ismapped())

    def test_copy_has_button_check_and_fading_toast(self):
        app = self.app
        button = app.command_buttons[1]
        button.invoke()
        app.update()
        self.assertEqual(app.clipboard_get(), '/function vidbuild:check')
        self.assertTrue(button._copied)
        self.assertTrue(app.copy_toast.winfo_ismapped())
        self.pump_until(lambda: not button._copied and not app.copy_toast.winfo_ismapped(), timeout=3)
        self.assertIsNone(app.copy_toast._tick_id)

    def test_clear_selected_video_preserves_settings_and_can_reupload(self):
        app = self.app
        browse_size = (app.browse_btn.winfo_width(), app.browse_btn.winfo_height())
        first = self.make_video('clear.mp4')
        with patch('app.filedialog.askopenfilename', return_value=str(first)):
            app.choose_video()
        self.pump_until(lambda: app.preview.frame_serial >= 2)
        decoder = app.preview.decoder
        original_output = app.output.get()
        app.building_hints.set('Keep the stone roof')
        self.pump_until(lambda: app.clear_video_btn.winfo_ismapped())
        app.clear_video_btn.invoke()
        app.update()
        self.assertEqual(app.video.get(), '')
        self.assertFalse(app.preview.winfo_ismapped())
        self.assertTrue(app.video_prompt.winfo_ismapped())
        self.assertEqual(app.browse_btn.cget('text'), 'Browse files')
        self.assertEqual((app.browse_btn.winfo_width(), app.browse_btn.winfo_height()), browse_size)
        self.assertIsNone(app.preview.decoder)
        self.assertFalse(decoder.thread.is_alive())
        self.assertEqual(app.output.get(), original_output)
        self.assertEqual(app.building_hints.get(), 'Keep the stone roof')
        with patch('app.filedialog.askopenfilename', return_value=str(first)):
            app.choose_video()
        self.pump_until(lambda: app.preview.frame_serial >= 2)
        self.assertTrue(app.preview.winfo_ismapped())

    def test_cover_preview_is_corner_clipped_and_matches_well(self):
        app = self.app
        video = self.make_video('round.mp4', (160, 120))
        app.video.set(str(video))
        app._back_to_video()
        self.pump_until(lambda: app.preview.frame_serial >= 2)
        preview = app.preview
        w, h = preview.winfo_width(), preview.winfo_height()
        self.assertEqual(preview.display_bounds, (0, 0, w, h))
        self.assertEqual(preview.current_frame.size, (w, h))
        self.assertEqual(preview._round_mask.getpixel((0, 0)), 0)
        self.assertEqual(preview._round_mask.getpixel((w//2, h//2)), 255)
        self.assertEqual(preview.current_frame.getpixel((0, 0)), preview._matte.getpixel((0, 0)))
        self.assertNotEqual(preview.current_frame.getpixel((w//2, h//2)),
                            preview._matte.getpixel((w//2, h//2)))

    def test_status_changes_without_feedback_visibility_change_do_not_repaint_all_glass(self):
        app = self.app
        with patch.object(app, '_queue_glass_refresh') as refresh:
            for idx in range(6):
                app.status.set(f'Selected video: sample_{idx}.mp4')
            app.update()
            refresh.assert_not_called()

    def test_invalid_preview_keeps_change_video_and_reports_inside_settings(self):
        app = self.app
        invalid = self.root/'invalid.mp4'
        invalid.write_bytes(b'not a video')
        with patch('app.filedialog.askopenfilename', return_value=str(invalid)):
            app.choose_video()
        self.pump_until(lambda: app.status.get().startswith('Preview unavailable:'))
        self.assertEqual(app._content, 'video')
        self.assertTrue(app.browse_btn.winfo_ismapped())
        self.assertEqual(app.browse_btn.cget('text'), 'Change video')
        self.assertTrue(app.feedback_frame.winfo_ismapped())

    def test_hidden_video_resumes_without_catching_up_hidden_playback_time(self):
        app = self.app
        video = self.make_video('hide.mp4')
        app.video.set(str(video))
        app._back_to_video()
        self.pump_until(lambda: app.preview.frame_serial >= 2)
        app.preview.suspend()
        app.preview._video_deadline = time.monotonic()-3600
        app.preview._timer.cancel(app.preview._after)
        app.preview._after = None
        app.preview._pump()
        self.assertIsNone(app.preview._video_deadline)
        serial = app.preview.frame_serial
        app.preview.resume()
        self.pump_until(lambda: app.preview.frame_serial > serial)
        self.assertLess(app.preview.loop_count, 3)

    def test_switching_video_resets_old_media_clock_and_starts_at_first_frames(self):
        app = self.app
        first = self.make_video('clock_first.mp4')
        second = self.make_video('clock_second.mp4')
        app.video.set(str(first))
        app._back_to_video()
        self.pump_until(lambda: app.preview.frame_serial >= 2)
        app.preview._video_deadline = time.monotonic()-3600
        app.video.set(str(second))
        app._back_to_video()
        self.assertIsNone(app.preview._video_deadline)
        self.pump_until(lambda: app.preview.frame_serial >= 1)
        self.assertLess(app.preview.frame_index, 4)
        self.assertEqual(app.preview.loop_count, 0)

    def test_burst_of_worker_notifications_yields_and_preserves_message_order(self):
        app = self.app
        handled = []
        for index in range(200):
            app._ui_queue.put(('success', (handled.append, index)))
        now = [0.]
        def advancing_clock():
            now[0] += .0007
            return now[0]
        with patch('app.time.monotonic', side_effect=advancing_clock):
            app._drain_ui_queue()
        self.assertGreater(len(handled), 0)
        self.assertLess(len(handled), 200)
        self.assertEqual(handled, list(range(len(handled))))
        self.assertEqual(app._ui_queue.qsize(), 200-len(handled))


if __name__ == '__main__':
    mp.freeze_support()
    unittest.main()
