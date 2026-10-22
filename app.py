"""MimetiCraft 0.3.1: local Tkinter GUI, ChatGPT-plan sign in by default."""
import os
from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from chatgpt_auth import ChatGPTAuth
from video2pack import DEFAULT_FRAMES, MAX_FRAMES, run


GAME_COMMANDS = ('/reload', '/function vidbuild:check',
                 '/function vidbuild:build', '/function vidbuild:undo')


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('MimetiCraft v0.3.1 — Java 26.3')
        self.geometry('780x560')
        self.minsize(740, 530)
        self.auth = ChatGPTAuth()
        self.video = tk.StringVar()
        self.output = tk.StringVar(value=str(Path.home() / 'Downloads' / 'video_build.zip'))
        self.provider = tk.StringVar(value='chatgpt')
        self.api_model = tk.StringVar(value='gpt-5.5')
        self.api_key = tk.StringVar(value=os.getenv('OPENAI_API_KEY', ''))
        self.model = tk.StringVar(value='')
        self.model_mapping = {}
        self.frames = tk.IntVar(value=DEFAULT_FRAMES)
        self.building_hints = tk.StringVar(value='')
        selected = self.auth.selected()
        self.account = tk.StringVar(value=selected.email if selected else 'None — sign in below')
        self.account_choice = tk.StringVar()
        self.account_ids = {}
        self.status = tk.StringVar(value='Choose the current ChatGPT account, select a video, then generate a Data Pack ZIP.')
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text='AI connection').grid(row=0, column=0, sticky='w')
        providers = ttk.Frame(frame)
        providers.grid(row=0, column=1, columnspan=2, sticky='w')
        ttk.Radiobutton(providers, text='Use ChatGPT plan (recommended)', variable=self.provider,
                        value='chatgpt', command=self._provider_changed).pack(side='left', padx=(0, 16))
        ttk.Radiobutton(providers, text='API key (optional)', variable=self.provider,
                        value='api', command=self._provider_changed).pack(side='left')

        self.login_frame = ttk.LabelFrame(frame, text='ChatGPT connection', padding=9)
        self.login_frame.grid(row=1, column=0, columnspan=3, sticky='ew', pady=(8, 5))
        self.login_frame.columnconfigure(1, weight=1)
        ttk.Label(self.login_frame, text='Saved accounts').grid(row=0, column=0, sticky='w')
        self.account_box = ttk.Combobox(self.login_frame, textvariable=self.account_choice,
                                        state='readonly', width=42)
        self.account_box.grid(row=0, column=1, columnspan=2, sticky='ew', padx=8)
        self.account_box.bind('<<ComboboxSelected>>', lambda _event: self.switch_account())
        self.account_box.bind('<Button-1>', self._schedule_account_menu_binding, add='+')
        self.account_box.bind('<ButtonRelease-1>', self._schedule_account_menu_binding, add='+')
        self.account_box.bind('<Button-3>', self._account_context_menu)
        self.bind_all('<Button-3>', self._account_global_context_menu, add='+')
        self.login_btn = ttk.Button(self.login_frame, text='Sign in with ChatGPT', command=self.sign_in)
        self.login_btn.grid(row=1, column=0, sticky='w', pady=7)
        self.models_btn = ttk.Button(self.login_frame, text='Refresh current account models', command=self.refresh_models)
        self.models_btn.grid(row=1, column=1, sticky='w', padx=8)
        self.new_account_btn = ttk.Button(self.login_frame, text='Add account', command=lambda: self.sign_in(add=True))
        self.new_account_btn.grid(row=1, column=2, sticky='w', padx=8)

        self.api_frame = ttk.LabelFrame(frame, text='Optional API key', padding=9)
        self.api_frame.columnconfigure(1, weight=1)
        ttk.Label(self.api_frame, text='API key').grid(row=0, column=0, sticky='w')
        ttk.Entry(self.api_frame, textvariable=self.api_key, show='*').grid(row=0, column=1, sticky='ew', padx=8)
        ttk.Label(self.api_frame, text='Model').grid(row=1, column=0, sticky='w', pady=4)
        ttk.Entry(self.api_frame, textvariable=self.api_model).grid(row=1, column=1, sticky='ew', padx=8)

        ttk.Label(frame, text='Model').grid(row=2, column=0, sticky='w', pady=9)
        self.model_box = ttk.Combobox(frame, textvariable=self.model, state='readonly', width=47)
        self.model_box.grid(row=2, column=1, columnspan=2, sticky='ew', padx=9)

        ttk.Label(frame, text='Building video').grid(row=3, column=0, sticky='w', pady=9)
        ttk.Entry(frame, textvariable=self.video).grid(row=3, column=1, sticky='ew', padx=9)
        ttk.Button(frame, text='Browse…', command=self.choose_video).grid(row=3, column=2, sticky='w')

        ttk.Label(frame, text='Output ZIP').grid(row=4, column=0, sticky='w', pady=9)
        ttk.Entry(frame, textvariable=self.output).grid(row=4, column=1, sticky='ew', padx=9)
        ttk.Button(frame, text='Save as…', command=self.choose_output).grid(row=4, column=2, sticky='w')

        ttk.Label(frame, text='Sample frames').grid(row=5, column=0, sticky='w', pady=9)
        ttk.Spinbox(frame, from_=2, to=MAX_FRAMES, textvariable=self.frames, width=6).grid(row=5, column=1, sticky='w', padx=9)
        ttk.Label(frame, text=f'Default {DEFAULT_FRAMES} (2-{MAX_FRAMES})').grid(row=5, column=2, sticky='w')

        self.generate_btn = ttk.Button(frame, text='Generate Minecraft Data Pack ZIP', command=self.generate)
        self.generate_btn.grid(row=6, column=1, pady=(15, 8))
        self.command_panel = self._command_panel(frame)
        self.command_panel.grid(row=7, column=0, columnspan=3, sticky='ew', pady=6)
        ttk.Button(frame, text='Plan usage / limits', command=lambda: webbrowser.open('https://chatgpt.com/#settings/Usage')).grid(row=8, column=0, columnspan=3, sticky='w')
        self._refresh_account_choices()
        self._provider_changed()

    def _refresh_account_choices(self):
        """Show every locally saved registration without exposing credentials."""
        self.account_ids = {}
        labels = []
        for account in self.auth.accounts():
            label = account.email or 'ChatGPT account'
            # Keep duplicate emails selectable if an account provider returns one.
            if label in self.account_ids:
                label = f'{label} ({account.subject[:8]})'
            self.account_ids[label] = account.client_id
            labels.append(label)
        self.account_box['values'] = labels
        selected = self.auth.selected()
        selected_label = next((label for label, client_id in self.account_ids.items()
                               if client_id == (selected.client_id if selected else None)), '')
        self.account_choice.set(selected_label)
        self.login_btn.configure(text=('Reauthorize current account' if selected
                                       else 'Sign in with ChatGPT'))

    def _schedule_account_menu_binding(self, _event=None):
        """Attach the context menu to the native list shown by ttk.Combobox."""
        self.after(80, self._bind_account_popdown)

    def _bind_account_popdown(self):
        try:
            popdown = self.tk.call('ttk::combobox::PopdownWindow', str(self.account_box))
        except (tk.TclError, KeyError):
            return
        listbox = self._find_listbox(popdown)
        if listbox is None:
            # The native popdown can finish mapping a few milliseconds after
            # the mouse event; retry once while it is open.
            self.after(50, self._bind_account_popdown)
            return
        listbox.bind('<Button-3>', self._account_list_context_menu, add='+')

    def _account_global_context_menu(self, event):
        """Fallback for Tk builds that do not preserve a widget binding on popdown."""
        try:
            popdown = self.tk.call('ttk::combobox::PopdownWindow', str(self.account_box))
        except (tk.TclError, KeyError):
            return
        listbox = self._find_listbox(popdown)
        if listbox is not None and event.widget == listbox:
            self._account_list_context_menu(event)
            return 'break'

    def _find_listbox(self, widget_path):
        """Find the Listbox used by a themed Combobox popdown."""
        try:
            widget = self.nametowidget(widget_path)
        except (tk.TclError, KeyError):
            return None
        try:
            if self.tk.call('winfo', 'class', widget_path) == 'Listbox':
                return widget
            children = self.tk.call('winfo', 'children', widget_path)
        except tk.TclError:
            return None
        for child in children:
            found = self._find_listbox(child)
            if found is not None:
                return found
        return None

    def _account_context_menu(self, event):
        client_id = self.account_ids.get(self.account_choice.get())
        if client_id:
            self._show_account_menu(event.x_root, event.y_root, client_id)

    def _account_list_context_menu(self, event):
        listbox = event.widget
        index = listbox.nearest(event.y)
        values = list(self.account_box['values'])
        if not 0 <= index < len(values):
            return
        self.account_box.current(index)
        client_id = self.account_ids.get(values[index])
        if client_id:
            self._show_account_menu(event.x_root, event.y_root, client_id)
            return 'break'

    def _show_account_menu(self, x, y, client_id):
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label='Delete account', command=lambda: self.delete_account(client_id))
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def delete_account(self, client_id):
        account = next((a for a in self.auth.accounts() if a.client_id == client_id), None)
        if not account:
            return
        if not messagebox.askyesno('Delete saved account',
                                   f'Delete the saved account {account.email}?\n\n'
                                   'This removes its local login record and credential.',
                                   parent=self):
            return
        try:
            self.auth.delete_account(client_id)
        except Exception as exc:
            self._error(str(exc))
            return
        self.model_mapping = {}
        self.model_box['values'] = ()
        self.model.set('')
        selected = self.auth.selected()
        self.account.set(selected.email if selected else 'None — sign in below')
        self._refresh_account_choices()
        self.status.set('Saved account deleted.')

    def switch_account(self):
        """Activate a saved account and load its models without opening a browser."""
        client_id = self.account_ids.get(self.account_choice.get())
        if not client_id:
            self.status.set('No saved ChatGPT account is available. Use another account to sign in.')
            return
        self.auth.select(client_id)
        account = self.auth.selected()
        self.account.set(account.email if account else 'None — sign in below')
        self._task('Switched account; fetching models for ' + (account.email if account else 'the selected account') + '…',
                   self.auth.models,
                   self._models_ready)

    def edit_building_hints(self):
        dialog = tk.Toplevel(self)
        dialog.title('Building hints')
        dialog.transient(self)
        content = ttk.Frame(dialog, padding=16)
        content.pack(fill='both', expand=True)
        ttk.Label(content, text='Optional instructions. Hints are recorded separately from video evidence.', wraplength=500).pack(anchor='w')
        text = tk.Text(content, width=66, height=8, wrap='word')
        text.insert('1.0', self.building_hints.get())
        text.pack(fill='both', expand=True, pady=8)
        def save():
            value = text.get('1.0', 'end-1c')
            if len(value) > 8192:
                messagebox.showerror('Building hints', 'Use at most 8192 characters.', parent=dialog)
                return
            self.building_hints.set(value)
            dialog.destroy()
        ttk.Button(content, text='Save', command=save).pack(anchor='e')

    def _copy_text(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)

    def _command_panel(self, parent):
        panel = ttk.LabelFrame(parent, text='In-game commands', padding=8)
        panel.columnconfigure(0, weight=1)
        for row, command in enumerate(GAME_COMMANDS):
            entry = ttk.Entry(panel)
            entry.insert(0, command)
            entry.configure(state='readonly')
            entry.grid(row=row, column=0, sticky='ew', pady=2)
            ttk.Button(panel, text='Copy', command=lambda value=command: self._copy_text(value)).grid(row=row, column=1, padx=(8, 0), pady=2)
        return panel

    def _provider_changed(self):
        if self.provider.get() == 'chatgpt':
            self.api_frame.grid_remove()
            self.login_frame.grid(row=1, column=0, columnspan=3, sticky='ew', pady=(8, 5))
            self.model_box.configure(state='readonly')
        else:
            self.login_frame.grid_remove()
            self.api_frame.grid(row=1, column=0, columnspan=3, sticky='ew', pady=(8, 5))
            self.model_box.configure(state='disabled')

    def choose_video(self):
        path = filedialog.askopenfilename(filetypes=[('Video files', '*.mp4 *.mov *.mkv *.avi'), ('All files', '*.*')])
        if path:
            self.video.set(path)

    def choose_output(self):
        path = filedialog.asksaveasfilename(defaultextension='.zip', filetypes=[('ZIP files', '*.zip')])
        if path:
            self.output.set(path)

    def _task(self, text, work, success):
        self.generate_btn.configure(state='disabled')
        self.login_btn.configure(state='disabled')
        self.models_btn.configure(state='disabled')
        self.new_account_btn.configure(state='disabled')
        self.account_box.configure(state='disabled')
        self.status.set(text)

        def runner():
            try:
                value = work()
            except Exception as exc:
                msg = str(exc)
                self.after(0, lambda: self._error(msg))
            else:
                self.after(0, lambda: success(value))

        threading.Thread(target=runner, daemon=True).start()

    def _ready(self):
        for btn in (self.generate_btn, self.login_btn, self.models_btn, self.new_account_btn):
            btn.configure(state='normal')
        self.account_box.configure(state='readonly')

    def _error(self, msg):
        self._ready()
        self.status.set('Error: ' + msg.splitlines()[0])
        dialog = tk.Toplevel(self)
        dialog.title('MimetiCraft - error details')
        dialog.transient(self)
        content = ttk.Frame(dialog, padding=16)
        content.pack(fill='both', expand=True)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        details = tk.Text(content, width=76, height=15, wrap='word')
        details.insert('1.0', msg)
        details.configure(state='disabled')
        details.grid(row=0, column=0, columnspan=2, sticky='nsew')
        ttk.Button(content, text='Copy error details', command=lambda: self._copy_text(msg)).grid(row=1, column=0, sticky='w', pady=(12, 0))
        ttk.Button(content, text='Close', command=dialog.destroy).grid(row=1, column=1, sticky='e', pady=(12, 0))

    def sign_in(self, add=False):
        self._task('Opening the browser for ChatGPT authorization…',
                   lambda: (self.auth.sign_in(add_account=add), self.auth.models()),
                   self._signed_in)

    def _signed_in(self, data):
        account, models = data
        self.account.set(account.email)
        self._refresh_account_choices()
        self._load_models(models)
        self._ready()
        self.status.set('ChatGPT connected and available models loaded. Select a video to begin.')

    def refresh_models(self):
        self._task('Checking saved ChatGPT authorization and fetching available models…',
                   self.auth.models, self._models_ready)

    def _models_ready(self, models):
        self._load_models(models)
        account = self.auth.selected()
        self.account.set(account.email if account else 'None — sign in below')
        self._ready()
        self.status.set(f'Found {len(models)} available ChatGPT models.')

    def _load_models(self, models):
        self.model_mapping = {f'{title} [{slug}]': slug for title, slug in models}
        entries = list(self.model_mapping)
        self.model_box['values'] = entries
        # Prefer a GPT-6 family model when this account lists one; otherwise
        # retain the server's advertised ordering (never invent a model name).
        best = next((name for name in entries if 'gpt-6' in self.model_mapping[name].lower()), entries[0])
        self.model.set(best)

    def generate(self):
        if not Path(self.video.get()).is_file():
            messagebox.showerror('Missing video', 'Select a Minecraft construction video first.')
            return
        mode = self.provider.get()
        if mode == 'chatgpt' and self.model.get() not in self.model_mapping:
            messagebox.showerror('Missing model', 'Sign in with ChatGPT and click Refresh models first.')
            return
        if mode == 'api' and not self.api_key.get():
            messagebox.showerror('Missing API key', 'Enter an API key or switch to ChatGPT plan mode.')
            return
        try:
            frames = self.frames.get()
        except tk.TclError:
            messagebox.showerror('Invalid frame count', f'Frame count must be between 2 and {MAX_FRAMES}.')
            return
        if not 2 <= frames <= MAX_FRAMES:
            messagebox.showerror('Invalid frame count', f'Frame count must be between 2 and {MAX_FRAMES}.')
            return
        params = {'video': self.video.get(), 'out': self.output.get(), 'frames': frames,
                  'auth_mode': mode,
                  'building_hints': self.building_hints.get(),
                  'model': self.model_mapping[self.model.get()] if mode == 'chatgpt' else self.api_model.get()}
        if mode == 'api':
            params['api_key'] = self.api_key.get()
        self._task('Sampling 10% extra frames and filtering similar images locally, then reconstructing the building and writing the ZIP…',
                   lambda: run(**params, progress=lambda text: self.after(0, lambda message=text: self.status.set(message)),
                               **({'access_token': self.auth.access_token()} if mode == 'chatgpt' else {})),
                   self._generated)

    def _generated(self, summary):
        self._ready()
        skipped = summary.get('skipped_regions', 0)
        note = f' | {skipped} unknown/preserved region(s) left untouched' if skipped else ''
        self.status.set(f"Created {summary['zip']} · {summary['commands']} block commands{note}")
        dialog = tk.Toplevel(self)
        dialog.title('Data Pack ZIP ready')
        dialog.transient(self)
        content = ttk.Frame(dialog, padding=16)
        content.pack(fill='both', expand=True)
        content.columnconfigure(0, weight=1)
        ttk.Label(content, text='Place this ZIP in your world/datapacks folder:').grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 8))
        path = ttk.Entry(content, width=64)
        path.insert(0, summary['zip'])
        path.configure(state='readonly')
        path.grid(row=1, column=0, sticky='ew')
        ttk.Button(content, text='Copy path', command=lambda: self._copy_text(summary['zip'])).grid(row=1, column=1, padx=(8, 0))
        self._command_panel(content).grid(row=2, column=0, columnspan=2, sticky='ew', pady=12)
        info = 'Undo removes generated blocks, not the previous terrain. Back up your world.'
        if skipped:
            info = f'{skipped} unknown/preserved region(s) were left untouched. Review the result or provide Building hints.\n\n' + info
        ttk.Label(content, text=info, wraplength=520).grid(row=3, column=0, columnspan=2, sticky='w')
        ttk.Button(content, text='Close', command=dialog.destroy).grid(row=4, column=0, columnspan=2, pady=(12, 0))


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='MimetiCraft desktop app')
    parser.add_argument('--video', help='Preselect a construction video')
    parser.add_argument('--out', help='Preselect the output ZIP path')
    args = parser.parse_args()
    app = App()
    if args.video:
        app.video.set(str(Path(args.video).resolve()))
    if args.out:
        app.output.set(str(Path(args.out).resolve()))
    app.mainloop()
