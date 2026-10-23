"""Reference-proportioned UI assembled from editable native controls and vector art."""
from PIL import ImageTk
import tkinter as tk

from video2pack import MAX_FRAMES
from video_preview import VideoPreview
from ui_motion import ActivityBar, CopyToast
from ui_widgets import (ACCENT, BORDER, CARD, FIELD, MUTED, WHITE, GlassFrame, GlassLabel,
                        IconView, RoundedButton, RoundedField, RoundedSurface, parent_pixels)


def build_reference_ui(app, game_commands):
    a = app
    scale = a._dpi_scale
    a.canvas = tk.Canvas(a, bg='#0A101B', highlightthickness=0)
    a.canvas.place(relwidth=1, relheight=1)
    a.canvas._particle_target = True
    from pathlib import Path
    from PIL import Image
    # JPEG decoding is lazy; the raster worker loads pixels on first use.
    a._scene_source = Image.open(Path(__file__).parent/'assets/scenery_2560.jpg')
    a._background_item = a.canvas.create_image(0, 0, anchor='nw')
    a.bind('<Configure>', a._schedule_background)
    a.left_card = RoundedSurface(a, radius=18, padding=0, scale=scale)
    a.left_card.opacity = .62
    a.left = a.left_card.body
    a.video_heading = a._label(a.left, 'Building video', 20, True)
    a.video_heading_icon = IconView(a.left, 'video', glow=True)
    a.confirm_frame = RoundedSurface(a.left, fill='#352430', outline='#704051', radius=14, padding=14, scale=scale)
    a.confirm_label = GlassLabel(a.confirm_frame.body, bg='#352430', fg=WHITE, font=a._font(), width=1)
    a.confirm_label.pack(fill='x')
    a.confirm_actions = GlassFrame(a.confirm_frame.body, bg='#352430')
    a.confirm_actions.pack(fill='x', pady=(10, 0))
    a.confirm_yes = a._button(a.confirm_actions, 'Confirm', a._accept_confirmation, danger=True)
    a.confirm_yes.pack(side='left')
    a.confirm_no = a._button(a.confirm_actions, 'Cancel', a._cancel_confirmation)
    a.confirm_no.pack(side='left', padx=10)
    a.confirm_frame.bind('<Configure>', a._layout_confirmation, add='+')

    a.video_frame = GlassFrame(a.left, bg=CARD)
    a.video_frame.columnconfigure(0, weight=1)
    a.video_frame.rowconfigure(0, weight=1)
    a.prompt_card = RoundedSurface(a.video_frame, radius=22, padding=14, scale=scale)
    a.prompt_card.grid(row=0, column=0, sticky='nsew')
    # The entire video well gets independent frosted glass, not a clear cutout.
    a.prompt_card.opacity, a.prompt_card.blur, a.prompt_card.dashed = .33, 8*scale, True
    a.prompt_card.backdrop_provider = lambda: a._scene_pixels.crop((
        a.prompt_card.winfo_rootx()-a.winfo_rootx(), a.prompt_card.winfo_rooty()-a.winfo_rooty(),
        a.prompt_card.winfo_rootx()-a.winfo_rootx()+max(1, a.prompt_card.winfo_width()),
        a.prompt_card.winfo_rooty()-a.winfo_rooty()+max(1, a.prompt_card.winfo_height()))) if hasattr(a, '_scene_pixels') else parent_pixels(a.prompt_card)
    a.prompt_card.backdrop_key = lambda: (id(getattr(a, '_scene_pixels', None)),
        a.prompt_card.winfo_rootx()-a.winfo_rootx(), a.prompt_card.winfo_rooty()-a.winfo_rooty(),
        a.prompt_card.winfo_width(), a.prompt_card.winfo_height())
    a.video_prompt = GlassFrame(a.prompt_card.body, bg=CARD)
    a.video_prompt.place(relx=.5, rely=.49, relwidth=.96, anchor='center')
    a.video_prompt.columnconfigure(0, weight=1)
    a.upload_tile = RoundedSurface(a.video_prompt, radius=12, padding=16, scale=scale)
    a.upload_tile.opacity = .48
    a.upload_tile.grid(row=0, column=0)
    a.upload_icon = IconView(a.upload_tile.body, 'upload')
    a.upload_icon.place(relwidth=1, relheight=1)
    a.video_label = a._label(a.video_prompt, 'Select a Minecraft building video', 20, True, width=1)
    a.video_label.configure(anchor='center', justify='center')
    a.video_label.grid(row=1, column=0, sticky='ew')
    a.video_formats = a._label(a.video_prompt, 'MP4 · MOV · MKV · AVI', 14)
    a.video_formats.grid(row=2, column=0)
    a.browse_btn = a._button(a.video_prompt, 'Browse files', a.choose_video, primary=True)
    a.browse_btn.icon = 'folder'
    a.browse_btn.grid(row=3, column=0)
    a.preview = VideoPreview(a.prompt_card.body, on_error=a._preview_failed,
                             on_frame=a._layout_preview_controls, on_progress=a._update_preview_progress)
    a.preview_bar = RoundedSurface(a.prompt_card.body, fill=FIELD, radius=10, padding=4, scale=scale)
    a.preview_bar.opacity = .7
    a.preview_bar.backdrop_provider = a._preview_controls_backdrop
    a.preview_bar.body.columnconfigure(0, weight=1)
    a.preview_name = a._label(a.preview_bar.body, '', 12, width=1)
    a.preview_name.grid(row=0, column=0, sticky='ew', padx=(0, a._u(10)))
    a.pause_preview_btn = a._button(a.preview_bar.body, 'Pause', a._toggle_preview, compact=True, micro=True)
    a.pause_preview_btn.grid(row=0, column=1, padx=(0, a._u(6)))
    a.change_video_btn = a._button(a.preview_bar.body, 'Change', a.choose_video, compact=True, micro=True)
    a.change_video_btn.grid(row=0, column=2, padx=(0, a._u(5)))
    a.clear_video_btn = a._button(a.preview_bar.body, 'Clear', a.clear_video, compact=True, micro=True)
    a.clear_video_btn.grid(row=0, column=3)
    a.preview_progress = tk.Scale(
        a.preview_bar.body, from_=0.0, to=1.0, resolution=0.001, orient='horizontal',
        showvalue=0, highlightthickness=0, bd=0, relief='flat', sliderrelief='flat',
        bg=FIELD, troughcolor='#7771A5', activebackground=ACCENT, fg=WHITE,
        cursor='hand2', command=a._seek_preview,
    )
    a.preview_progress.grid(row=1, column=0, columnspan=4, sticky='ew', pady=(a._u(2), 0))
    a.details = RoundedField(a.video_frame, 'text', font=a._font(), scale=scale, wrap='word', height=7, state='disabled')
    a.detail_actions = GlassFrame(a.video_frame, bg=CARD)
    a.detail_copy = a._button(a.detail_actions, 'Copy details', a._copy_details)
    a.detail_copy.pack(side='left')
    a._button(a.detail_actions, 'Back to video', a._back_to_video).pack(side='left', padx=8)

    a.commands_card = RoundedSurface(a.left, radius=18, padding=0, scale=scale)
    a.commands_card.opacity = .22
    commands = a.commands_card.body
    a.commands_heading = a._label(commands, 'In-game commands', 17, True)
    a.commands_heading_icon = IconView(commands, 'terminal', glow=True)
    a.command_buttons = []
    for command in game_commands:
        button = a._button(commands, command, lambda value=command: a._copy_text(value))
        button.configure(command=lambda value=command, target=button: a._copy_text(value, source_button=target))
        button.command_style = True
        a.command_buttons.append(button)

    a.right_card = RoundedSurface(a, radius=18, padding=0, scale=scale)
    a.right_card.opacity = .67
    a.right = a.right_card.body
    a.settings_heading = a._label(a.right, 'Settings', 20, True)
    a.settings_heading_icon = IconView(a.right, 'gear')
    a.settings_canvas = tk.Canvas(a.right, bg=CARD, highlightthickness=0, width=1, height=1)
    a._settings_background_item = a.settings_canvas.create_image(0, 0, anchor='nw')
    a.settings_canvas._particle_target = True
    a.form = GlassFrame(a.settings_canvas, bg=CARD)
    a._form_item = a.settings_canvas.create_window(0, 0, window=a.form, anchor='nw')
    a.bind_all('<MouseWheel>', a._scroll_settings, add='+')
    a.mode_frame = GlassFrame(a.form, bg=CARD)
    a.mode_buttons = []
    for value, text in [('chatgpt', 'ChatGPT Plan'), ('api', 'API key')]:
        button = RoundedButton(a.mode_frame, text, lambda mode=value: a._select_provider(mode),
                               font=a._font(13, True), scale=scale)
        button.mode_value = value
        a.mode_buttons.append(button)

    a.account_frame = GlassFrame(a.form, bg=CARD)
    a.account_label = a._label(a.account_frame, 'Saved ChatGPT account')
    a.account_box = RoundedField(a.account_frame, 'combo', textvariable=a.account_choice,
                                 state='readonly', font=a._font(), scale=scale)
    a.account_box.bind('<<ComboboxSelected>>', lambda _: a.switch_account())
    a.account_box.bind('<Button-3>', a._account_context_menu)
    a.login_btn = a._button(a.account_frame, 'Sign in with ChatGPT', a.sign_in)
    a.login_btn.icon = 'refresh'
    a.new_account_btn = a._button(a.account_frame, 'Add account', lambda: a.sign_in(add=True))
    a.new_account_btn.icon = 'account'
    a.models_btn = a._button(a.account_frame, 'Refresh models', a.refresh_models)
    a.models_btn.icon = 'cube'
    a.remove_account_btn = a._button(a.account_frame, 'Remove selected', a._ask_delete_account)
    a.remove_account_btn.icon = 'trash'
    a.api_frame = GlassFrame(a.form, bg=CARD)
    a.api_label = a._label(a.api_frame, 'API key · separately billed API usage')
    a.api_key_box = a._entry(a.api_frame, a.api_key, show='•')
    a.form_labels = {key: a._label(a.form, caption) for key, caption in [
        ('model', 'Model'), ('frames', 'Sample frames (2–1200)'), ('output', 'Output ZIP'), ('hints', 'Building hints (optional)')]}
    a.model_box = RoundedField(a.form, 'combo', textvariable=a.model, state='readonly', font=a._font(), scale=scale)
    a.api_model_box = a._entry(a.form, a.api_model)
    a.frames_box = RoundedField(a.form, 'spin', from_=2, to=MAX_FRAMES, textvariable=a.frames,
                                font=a._font(), scale=scale)
    a.output_row = GlassFrame(a.form, bg=CARD)
    a.output_box = a._entry(a.output_row, a.output)
    a.output_btn = a._button(a.output_row, 'Browse…', a.choose_output)
    a.hints_box = a._entry(a.form, a.building_hints)
    a.divider = tk.Canvas(a.right, bg=CARD, highlightthickness=0, bd=0, width=1, height=1)
    a.generate_btn = a._button(a.right, 'Generate Data Pack', a.generate, primary=True)
    a.generate_btn.icon = 'sparkles'
    a._editable = [*a.mode_buttons, a.login_btn, a.new_account_btn, a.models_btn, a.remove_account_btn,
                   a.api_key_box, a.api_model_box, a.frames_box, a.output_box, a.output_btn,
                   a.hints_box, a.browse_btn, a.change_video_btn, a.clear_video_btn]
    a.feedback_frame = GlassFrame(a.right, bg=CARD)
    a.feedback_frame.columnconfigure(0, weight=1)
    a.status_label = GlassLabel(a.feedback_frame, textvariable=a.status, bg=CARD, fg=MUTED,
                                font=a._font(12), width=1)
    a.status_label.grid(row=0, column=0, sticky='ew')
    a.status_label.bind('<Configure>', lambda e: a.status_label.configure(wraplength=max(100, e.width)), add='+')
    a.elapsed_label = a._label(a.feedback_frame, '', 12)
    a.elapsed_label.grid(row=0, column=1, padx=8)
    a.cancel_btn = a._button(a.feedback_frame, 'Cancel generation', a.cancel_task)
    a.progress_bar = ActivityBar(a.feedback_frame, scale=scale)
    a.copy_toast = CopyToast(a, scale=scale)
    for option, value in [('background', FIELD), ('foreground', WHITE), ('selectBackground', '#4D426C'),
                           ('selectForeground', WHITE), ('font', a._font(13))]:
        a.option_add(f'*TCombobox*Listbox.{option}', value)
    a.status.trace_add('write', a._status_changed)
    a.bind_all('<FocusIn>', a._ensure_settings_visible, add='+')
    a.video_label.bind('<Configure>', lambda e: a.video_label.configure(wraplength=max(100, e.width-4)), add='+')
    a.after_idle(lambda: layout_reference_ui(a))


def layout_reference_ui(a):
    width, height = a.winfo_width(), a.winfo_height()
    if width < 100 or height < 100:
        return
    signature = (width, height, a.provider.get())
    if signature == getattr(a, '_layout_signature', None):
        return
    a._layout_signature = signature
    sx, sy = width/1586, height/948
    a._reference_scale = sx
    a._reference_y = sy
    def place(widget, box):
        x, y, w, h = box
        widget.place(x=round(x*sx), y=round(y*sy), width=max(1, round(w*sx)), height=max(1, round(h*sy)))
    def font(size, bold=False):
        return ('Segoe UI', -max(11, round(size*sx)), 'bold' if bold else 'normal')
    def surface(widget, radius, inset=0):
        widget.radius, widget._material_scale = round(radius*sx), sx
        padding = round(inset*sx)
        if padding != getattr(widget, '_layout_inset', None):
            widget.body.place(x=padding, y=padding, relwidth=1, relheight=1, width=-2*padding, height=-2*padding)
            widget._layout_inset = padding
    place(a.left_card, (32, 20, 912, 873))
    place(a.right_card, (962, 20, 594, 873))
    for card in (a.left_card, a.right_card):
        surface(card, 18)
        card.blur = 4*sx
    place(a.video_heading_icon, (29, 23, 36, 32))
    place(a.video_heading, (82, 19, 800, 38))
    place(a.settings_heading_icon, (26, 23, 31, 32))
    place(a.settings_heading, (70, 19, 490, 38))
    a.video_heading.configure(font=font(26, True))
    a.settings_heading.configure(font=font(26, True))
    place(a.video_frame, (17, 72, 877, 565))
    surface(a.prompt_card, 22, 14)
    a.prompt_card.blur = 8*sx
    a.upload_tile.configure(width=round(68*sx), height=round(68*sy))
    surface(a.upload_tile, 12, 17)
    a.video_label.configure(font=font(26, True))
    a.video_formats.configure(font=font(18), fg='#CCC8F0')
    a.video_label.grid_configure(pady=(round(21*sy), 0))
    a.video_formats.grid_configure(pady=(round(8*sy), round(15*sy)))
    a.browse_btn._scale = sx
    a.browse_btn.configure(font=font(18, True))
    a.browse_btn.configure(width=round(216*sx), height=round(54*sy))
    place(a.commands_card, (11, 658, 890, 200))
    surface(a.commands_card, 18)
    place(a.commands_heading_icon, (19, 16, 30, 30))
    place(a.commands_heading, (70, 17, 780, 35))
    a.commands_heading.configure(font=font(22, True))
    for index, button in enumerate(a.command_buttons):
        button._scale = sx
        button.configure(font=font(18))
        place(button, (17+435*(index%2), 58+69*(index//2), 422, 57))
    place(a.settings_canvas, (26, 72, 540, 660))
    a.settings_canvas.itemconfigure(a._form_item, width=round(540*sx), height=round(660*sy))
    a.settings_canvas.configure(scrollregion=(0, 0, round(540*sx), round(660*sy)))
    place(a.mode_frame, (0, 0, 540, 48))
    for index, button in enumerate(a.mode_buttons):
        button._scale = sx
        button.configure(font=font(17, True))
        place(button, (164*index, 0, 158 if index == 0 else 142, 48))
    chatgpt = a.provider.get() == 'chatgpt'
    if chatgpt:
        a.api_frame.place_forget()
        a.api_model_box.place_forget()
        place(a.account_frame, (0, 69, 540, 202))
        place(a.account_label, (0, 0, 540, 24))
        place(a.account_box, (0, 27, 540, 46))
        for index, button in enumerate((a.login_btn, a.new_account_btn, a.models_btn, a.remove_account_btn)):
            button._scale = sx
            button.configure(font=font(16))
            place(button, (276*(index%2), 85+64*(index//2), 264, 53))
        positions = dict(model=(291, 315), frames=(380, 405), output=(470, 494), hints=(564, 589))
        place(a.model_box, (0, 315, 540, 46))
    else:
        a.account_frame.place_forget()
        a.model_box.place_forget()
        place(a.api_frame, (0, 69, 540, 92))
        place(a.api_label, (0, 0, 540, 24))
        place(a.api_key_box, (0, 27, 540, 46))
        positions = dict(model=(185, 212), frames=(285, 312), output=(385, 412), hints=(485, 512))
        place(a.api_model_box, (0, 212, 540, 46))
    for key, (label_y, field_y) in positions.items():
        place(a.form_labels[key], (0, label_y, 540, 24))
        a.form_labels[key].configure(font=font(20))
    place(a.frames_box, (0, positions['frames'][1], 540, 46))
    place(a.output_row, (0, positions['output'][1], 540, 47))
    place(a.output_box, (0, 0, 426, 47))
    a.output_btn._scale = sx
    a.output_btn.configure(font=font(17))
    place(a.output_btn, (434, 0, 106, 47))
    place(a.hints_box, (0, positions['hints'][1], 540, 60))
    for field in (a.account_box, a.model_box, a.api_model_box, a.api_key_box, a.frames_box, a.output_box, a.hints_box):
        field._scale = sx
        field.configure(font=font(20))
        padding = ((round(18*sx), round((42 if field.kind in ('combo', 'spin') else 18)*sx)), round(5*sy))
        if padding != getattr(field, '_layout_padding', None):
            field.control.pack_configure(padx=padding[0], pady=padding[1])
            field._layout_padding = padding
    a.account_label.configure(font=font(20))
    a.api_label.configure(font=font(20))
    place(a.divider, (26, 749, 540, 1))
    a.divider.configure(bg='#30394B')
    a.generate_btn._scale = sx
    a.generate_btn.configure(font=font(20, True))
    place(a.generate_btn, (26, 779, 540, 66))
    layout_feedback(a)
    a._queue_glass_refresh()


def layout_feedback(a):
    """Only move the feedback pane when its actual geometry/visibility changes."""
    if not hasattr(a, '_reference_scale'):
        return False
    value = a.status.get()
    visible = a._busy or value.startswith(('Error:', 'Missing input:', 'Generation cancelled.',
                                          'Complete ChatGPT authorization', 'ChatGPT sign-in failed:',
                                          'Cancel generation before', 'Wait for the account', 'Preview unavailable:'))
    previous = getattr(a, '_feedback_geometry', None)
    if not visible:
        if previous is not None:
            a.feedback_frame.place_forget()
            a._feedback_geometry = None
            return True
        return False
    sx, sy = a._reference_scale, a._reference_y
    height = max(round(55*sy), a.feedback_frame.winfo_reqheight())
    if a._task_kind == 'generate':
        height = max(height, a._u(112))
    geometry = (round(26*sx), round(767*sy)-height, round(540*sx), height)
    if geometry != previous:
        a.feedback_frame.place(x=geometry[0], y=geometry[1], width=geometry[2], height=geometry[3])
        a.feedback_frame.lift()
        a._feedback_geometry = geometry
        return True
    return False
