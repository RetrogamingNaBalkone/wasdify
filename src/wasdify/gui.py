"""Native Tkinter frontend. Workers never access Tk objects."""
import argparse
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import filedialog, ttk

from .i18n import Translator
from .models import ApplyResult, Detection, Issue, Plan, WasdifyError
from .presentation import apply_text, detection_text, plan_text
from .service import Service


class Application:
    def __init__(self, root: tk.Tk, service: Service | None = None,
                 language: str | None = None):
        self.root = root
        self.service = service if service is not None else Service()
        self.tr = Translator(language)
        self.path = tk.StringVar(root)
        self.backup = tk.BooleanVar(root, True)
        self.language = tk.StringVar(root, self.tr.language)
        self.game_choice = tk.StringVar(root)
        self.manual_game_id: str | None = None
        self.detected_game_id: str | None = None
        self.patch_vars: dict[str, tk.BooleanVar] = {}
        self.patch_game_id: str | None = None
        self.plan: Plan | None = None
        self.last_result: Detection | Plan | ApplyResult | Issue | None = None
        self.busy = False
        self.queue: Queue = Queue()
        self.root.title("WASDify")
        self.root.geometry("920x760")
        self.root.minsize(700, 600)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.path.trace_add("write", self._invalidate)
        self._build()

    def _build(self) -> None:
        for child in self.root.winfo_children():
            child.destroy()
        tr = self.tr
        frame = ttk.Frame(self.root, padding=16)
        frame.grid(sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(5, weight=1)
        self.controls: list[tuple[ttk.Widget, str]] = []

        def register(widget, normal="normal"):
            self.controls.append((widget, normal))
            return widget

        ttk.Label(frame, text="WASDify", font=("TkDefaultFont", 20, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 12))
        language_frame = ttk.Frame(frame)
        language_frame.grid(row=0, column=1, columnspan=2, sticky="e")
        ttk.Label(language_frame, text=tr("language")).pack(side="left", padx=8)
        language_box = register(ttk.Combobox(language_frame, textvariable=self.language,
                                            values=("ru", "en"), width=5, state="readonly"), "readonly")
        language_box.pack(side="left")
        language_box.bind("<<ComboboxSelected>>", self._switch_language)

        ttk.Label(frame, text=tr("game")).grid(row=1, column=0, sticky="w", padx=(0, 12), pady=6)
        self.game_ids = [None] + [game.id for game in self.service.catalog.games]
        labels = [tr("auto_game")] + [game.title[tr.language] for game in self.service.catalog.games]
        self.game_choice.set(labels[self.game_ids.index(self.manual_game_id)])
        self.game_box = register(ttk.Combobox(frame, textvariable=self.game_choice,
                                             values=labels, state="readonly"), "readonly")
        self.game_box.grid(row=1, column=1, columnspan=2, sticky="ew", pady=6)
        self.game_box.bind("<<ComboboxSelected>>", self._select_game)

        ttk.Label(frame, text=tr("executable")).grid(row=2, column=0, sticky="w", padx=(0, 12), pady=6)
        self.path_entry = register(ttk.Entry(frame, textvariable=self.path))
        self.path_entry.grid(row=2, column=1, sticky="ew", pady=6)
        register(ttk.Button(frame, text=tr("browse"), command=self.browse)).grid(
            row=2, column=2, padx=(8, 0), pady=6)

        self.patch_frame = ttk.LabelFrame(frame, text=tr("patches"), padding=10)
        self.patch_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=10)
        game_id = self.manual_game_id or self.detected_game_id
        if game_id != self.patch_game_id:
            self.patch_vars.clear()
            self.patch_game_id = game_id
        if game_id:
            for patch in self.service.list_patches(game_id):
                variable = self.patch_vars.setdefault(patch.id, tk.BooleanVar(self.root, False))
                register(ttk.Checkbutton(self.patch_frame, text=patch.title[tr.language],
                                         variable=variable, command=self._invalidate)).pack(anchor="w", pady=(4, 0))
                status = tr("implemented" if patch.implemented else "placeholder")
                description = (patch.description[tr.language] + "\n" + status + " · "
                               + tr("dependencies") + ": " + (", ".join(patch.dependencies) or tr("none"))
                               + " · " + tr("conflicts") + ": " + (", ".join(patch.conflicts) or tr("none")))
                ttk.Label(self.patch_frame, text=description, wraplength=780).pack(anchor="w", padx=(24, 0), pady=(0, 6))
        else:
            ttk.Label(self.patch_frame, text=tr("choose_file"), wraplength=780).pack(anchor="w")

        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        actions.columnconfigure(0, weight=1)
        register(ttk.Checkbutton(actions, text=tr("backup"), variable=self.backup)).grid(row=0, column=0, sticky="w")
        self.check_button = register(ttk.Button(actions, text=tr("check"), command=self.check))
        self.check_button.grid(row=0, column=1, padx=8)
        self.apply_button = ttk.Button(actions, text=tr("apply"), command=self.apply)
        self.apply_button.grid(row=0, column=2)

        results = ttk.LabelFrame(frame, text=tr("result"), padding=8)
        results.grid(row=5, column=0, columnspan=3, sticky="nsew")
        results.rowconfigure(0, weight=1)
        results.columnconfigure(0, weight=1)
        self.result_widget = tk.Text(results, wrap="word", height=10,
                                     font="TkDefaultFont", padx=6, pady=6, takefocus=True)
        self.result_widget.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(results, command=self.result_widget.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.result_widget.configure(yscrollcommand=scrollbar.set)
        self.patch_frame.bind("<Configure>", self._wrap_descriptions)
        self._show_result()
        self._update_controls()

    def _wrap_descriptions(self, event) -> None:
        for child in self.patch_frame.winfo_children():
            if isinstance(child, ttk.Label):
                child.configure(wraplength=max(200, event.width - 60))

    def _switch_language(self, event=None) -> None:
        self.tr = Translator(self.language.get())
        self._build()

    def _select_game(self, event=None) -> None:
        self.manual_game_id = self.game_ids[self.game_box.current()]
        self.plan = None
        self.last_result = None
        self._build()

    def _invalidate(self, *args) -> None:
        self.plan = None
        self.last_result = Issue("selection_changed")
        if hasattr(self, "apply_button"):
            self._show_result()
            self._update_controls()

    def _update_controls(self) -> None:
        for widget, normal in self.controls:
            widget.configure(state="disabled" if self.busy else normal)
        self.apply_button.configure(state="normal" if not self.busy and self.plan and self.plan.ready else "disabled")

    def _show_result(self) -> None:
        result = self.last_result
        if self.busy:
            text = self.tr("working")
        elif isinstance(result, Plan):
            text = plan_text(result, self.service, self.tr)
        elif isinstance(result, Detection):
            text = detection_text(result, self.service, self.tr)
            text += "\n\n" + self.tr("no_patches" if result.game_id else "unknown_file")
        elif isinstance(result, ApplyResult):
            text = apply_text(result, self.tr)
        elif isinstance(result, Issue):
            text = self.tr.issue(result)
        else:
            text = self.tr("choose_file")
        self.result_widget.configure(state="normal")
        self.result_widget.delete("1.0", "end")
        self.result_widget.insert("1.0", text)
        self.result_widget.configure(state="disabled")

    def browse(self) -> None:
        filename = filedialog.askopenfilename(parent=self.root, title=self.tr("executable"),
            filetypes=((self.tr("executables"), "*.exe *.EXE"), (self.tr("all_files"), "*")))
        if filename:
            self.path.set(filename)
            self.check()

    def _start(self, operation) -> None:
        if self.busy:
            return
        self.busy = True
        self._show_result()
        self._update_controls()

        def run():
            try:
                result = operation()
            except WasdifyError as error:
                result = error.issue
            except Exception:
                result = Issue("unexpected_error")
            self.queue.put(result)

        Thread(target=run, daemon=True, name="wasdify-worker").start()
        self.root.after(30, self._poll)

    def _poll(self) -> None:
        try:
            result = self.queue.get_nowait()
        except Empty:
            self.root.after(30, self._poll)
            return
        self.busy = False
        self.last_result = result
        self.plan = result if isinstance(result, Plan) and result.ready else None
        detection = result.detection if isinstance(result, Plan) else result
        if isinstance(detection, Detection):
            self.detected_game_id = detection.game_id
        self._build()

    def check(self) -> None:
        if self.busy:
            return
        path = self.path.get().strip()
        if not path:
            self.last_result = Issue("select_file")
            self._show_result()
            self.path_entry.focus_set()
            return
        selected = tuple(key for key, variable in self.patch_vars.items() if variable.get())
        game_id = self.manual_game_id
        self.plan = None
        if selected:
            self._start(lambda: self.service.plan({"main": path}, selected, game_id))
        else:
            self._start(lambda: self.service.detect(path, game_id))

    def apply(self) -> None:
        if self.busy or self.plan is None or not self.plan.ready:
            return
        plan, backup = self.plan, self.backup.get()
        self.plan = None
        self._start(lambda: self.service.apply(plan, backup=backup))

    def close(self) -> None:
        # Do not destroy Tk while a file transaction is still running.
        if not self.busy:
            self.root.destroy()


def main(argv: list[str] | None = None, *, language: str | None = None) -> int:
    if language is None:
        parser = argparse.ArgumentParser(prog="wasdify-gui")
        parser.add_argument("--lang", choices=("ru", "en"))
        language = parser.parse_args(argv).lang
    try:
        root = tk.Tk()
    except tk.TclError as error:
        raise WasdifyError("gui_unavailable") from error
    Application(root, language=language)
    root.mainloop()
    return 0

