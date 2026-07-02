from __future__ import annotations

import csv
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Iterable

try:
    import winreg
except ImportError:  # pragma: no cover - Windows-only feature.
    winreg = None


APP_NAME = "AData Explorer"
MAX_EVIDENCE_FILES = 250


KNOWN_PATTERNS = {
    "adobe": "Adobe",
    "battle.net": "Battle.net",
    "blizzard": "Blizzard/Battle.net",
    "chrome": "Google Chrome",
    "chromium": "Chromium",
    "discord": "Discord",
    "ea desktop": "EA App",
    "electronic arts": "Electronic Arts",
    "epicgameslauncher": "Epic Games Launcher",
    "epic games": "Epic Games",
    "firefox": "Mozilla Firefox",
    "github": "GitHub",
    "google": "Google",
    "gog": "GOG",
    "league of legends": "League of Legends",
    "microsoft": "Microsoft",
    "minecraft": "Minecraft",
    "mozilla": "Mozilla",
    "nvidia": "NVIDIA",
    "obs": "OBS Studio",
    "opera software": "Opera",
    "riot games": "Riot Games",
    "roblox": "Roblox",
    "rockstar games": "Rockstar Games",
    "spotify": "Spotify",
    "steam": "Steam",
    "telegram": "Telegram",
    "ubisoft": "Ubisoft",
    "unity": "Unity",
    "unreal engine": "Unreal Engine",
    "valve": "Valve/Steam",
    "visualstudio": "Microsoft Visual Studio",
    "vscode": "Visual Studio Code",
    "whatsapp": "WhatsApp",
}


@dataclass
class ScanResult:
    location: str
    folder_name: str
    path: str
    size_bytes: int
    file_count: int
    folder_count: int
    likely_owner: str
    confidence: str
    reason: str
    errors: int


def appdata_roots() -> list[tuple[str, Path]]:
    roots: list[tuple[str, Path]] = []
    for label, env_name in (
        ("Roaming", "APPDATA"),
        ("Local", "LOCALAPPDATA"),
    ):
        value = os.environ.get(env_name)
        if value:
            roots.append((label, Path(value)))

    local_low = Path.home() / "AppData" / "LocalLow"
    if local_low.exists():
        roots.append(("LocalLow", local_low))
    return roots


def human_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def safe_scandir(path: Path):
    try:
        with os.scandir(path) as entries:
            yield from entries
    except (OSError, PermissionError):
        return


def folder_size(path: Path) -> tuple[int, int, int, int, list[Path]]:
    total = 0
    file_count = 0
    folder_count = 0
    errors = 0
    evidence_files: list[Path] = []
    stack = [path]
    seen = 0

    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_file(follow_symlinks=False):
                            stat = entry.stat(follow_symlinks=False)
                            total += stat.st_size
                            file_count += 1
                            if seen < MAX_EVIDENCE_FILES:
                                evidence_files.append(Path(entry.path))
                                seen += 1
                        elif entry.is_dir(follow_symlinks=False):
                            folder_count += 1
                            stack.append(Path(entry.path))
                    except (OSError, PermissionError):
                        errors += 1
        except (OSError, PermissionError):
            errors += 1

    return total, file_count, folder_count, errors, evidence_files


def read_installed_programs() -> dict[str, str]:
    if winreg is None:
        return {}

    roots = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    programs: dict[str, str] = {}

    for hive, key_path in roots:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                for index in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        subkey_name = winreg.EnumKey(key, index)
                        with winreg.OpenKey(key, subkey_name) as subkey:
                            name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                            if isinstance(name, str) and name.strip():
                                programs[normalize(name)] = name.strip()
                    except OSError:
                        continue
        except OSError:
            continue
    return programs


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def identify_owner(path: Path, location: str, evidence_files: Iterable[Path], installed: dict[str, str]) -> tuple[str, str, str]:
    parts = [part for part in path.parts if part]
    text = normalize(" ".join(parts[-4:]))

    for key, display_name in installed.items():
        if len(key) >= 4 and key in text:
            return display_name, "Alta", "Nome da pasta coincide com um programa instalado no Windows."

    for pattern, owner in KNOWN_PATTERNS.items():
        if normalize(pattern) in text:
            return owner, "Media", "Nome ou caminho bate com um padrao conhecido de aplicativo/jogo."

    file_names = [file.name.lower() for file in evidence_files]
    joined_files = " ".join(file_names)

    if location == "LocalLow":
        if any(name.endswith((".sav", ".save", ".dat", ".prefs")) for name in file_names):
            return "Jogo ou app Unity/LocalLow", "Media", "LocalLow e comum para jogos, especialmente Unity; foram encontrados arquivos de save/configuracao."
        return "Jogo ou app LocalLow", "Baixa", "A pasta fica em LocalLow, local comum para jogos e apps com dados isolados."

    if "shader" in joined_files or "dxcache" in joined_files or "vulkan" in joined_files:
        return "Cache grafico ou jogo", "Media", "Foram encontrados nomes relacionados a shader/cache grafico."

    if any(name.endswith((".exe", ".dll")) for name in file_names):
        return "Programa desconhecido com binarios", "Baixa", "Foram encontrados executaveis ou DLLs, mas sem correspondencia confiavel."

    if any(name.endswith((".sav", ".save")) for name in file_names):
        return "Jogo desconhecido", "Baixa", "Foram encontrados arquivos com extensao comum de save."

    if any(token in text for token in ("cache", "temp", "crash", "logs", "log")):
        return "Cache/logs temporarios", "Baixa", "O nome da pasta sugere cache, temporarios ou logs."

    return "Desconhecido", "Nenhuma", "Nao houve evidencia suficiente para identificar o dono."


def scan_appdata(progress_queue: queue.Queue, min_size_bytes: int = 0) -> list[ScanResult]:
    installed = read_installed_programs()
    results: list[ScanResult] = []

    roots = [(label, root) for label, root in appdata_roots() if root.exists()]
    total_targets = sum(1 for _, root in roots for entry in safe_scandir(root) if entry.is_dir(follow_symlinks=False))
    processed = 0

    for location, root in roots:
        for entry in safe_scandir(root):
            if not entry.is_dir(follow_symlinks=False) or entry.is_symlink():
                continue

            path = Path(entry.path)
            progress_queue.put(("status", f"Analisando {path}"))
            size, files, folders, errors, evidence = folder_size(path)
            processed += 1

            if size >= min_size_bytes:
                owner, confidence, reason = identify_owner(path, location, evidence, installed)
                results.append(
                    ScanResult(
                        location=location,
                        folder_name=path.name,
                        path=str(path),
                        size_bytes=size,
                        file_count=files,
                        folder_count=folders,
                        likely_owner=owner,
                        confidence=confidence,
                        reason=reason,
                        errors=errors,
                    )
                )

            progress_queue.put(("progress", processed, max(total_targets, 1)))

    results.sort(key=lambda item: item.size_bytes, reverse=True)
    progress_queue.put(("done", results))
    return results


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1280x780")
        self.minsize(1040, 620)

        self.results: list[ScanResult] = []
        self.progress_queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.sort_state: dict[str, bool] = {}
        self.file_sort_state: dict[str, bool] = {}
        self.current_folder: Path | None = None
        self.folder_history: list[Path] = []
        self.appdata_root_paths = [root.resolve() for _, root in appdata_roots() if root.exists()]

        self._build_ui()
        self.after(150, self._poll_queue)

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, padding=(10, 10, 10, 6))
        toolbar.pack(fill=tk.X)

        self.scan_button = ttk.Button(toolbar, text="Analisar AppData", command=self.start_scan)
        self.scan_button.pack(side=tk.LEFT)

        ttk.Label(toolbar, text="Tamanho minimo").pack(side=tk.LEFT, padx=(14, 4))
        self.min_size = ttk.Combobox(toolbar, width=10, state="readonly", values=("0 MB", "10 MB", "100 MB", "1 GB"))
        self.min_size.set("10 MB")
        self.min_size.pack(side=tk.LEFT)

        self.export_csv_button = ttk.Button(toolbar, text="Exportar CSV", command=self.export_csv, state=tk.DISABLED)
        self.export_csv_button.pack(side=tk.LEFT, padx=(14, 4))

        self.export_json_button = ttk.Button(toolbar, text="Exportar JSON", command=self.export_json, state=tk.DISABLED)
        self.export_json_button.pack(side=tk.LEFT)

        self.progress = ttk.Progressbar(toolbar, mode="determinate", length=220)
        self.progress.pack(side=tk.RIGHT)

        self.status = tk.StringVar(value="Pronto para analisar.")
        ttk.Label(self, textvariable=self.status, anchor=tk.W, padding=(10, 0, 10, 8)).pack(fill=tk.X)

        nav = ttk.Frame(self, padding=(10, 0, 10, 8))
        nav.pack(fill=tk.X)

        ttk.Button(nav, text="Relatorio", command=lambda: self.notebook.select(self.report_tab)).pack(side=tk.LEFT)
        ttk.Button(nav, text="Explorar", command=lambda: self.notebook.select(self.explorer_tab)).pack(side=tk.LEFT, padx=(6, 14))
        ttk.Button(nav, text="↓", width=3, command=self.go_back).pack(side=tk.LEFT)
        ttk.Button(nav, text="↑", width=3, command=self.go_up).pack(side=tk.LEFT, padx=(6, 8))

        self.path_var = tk.StringVar()
        self.path_entry = ttk.Entry(nav, textvariable=self.path_var)
        self.path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.path_entry.bind("<Return>", self.navigate_from_entry)
        ttk.Button(nav, text="Ir", command=self.navigate_from_entry).pack(side=tk.LEFT, padx=(8, 0))

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self.report_tab = ttk.Frame(self.notebook)
        self.explorer_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.report_tab, text="Relatorio")
        self.notebook.add(self.explorer_tab, text="Explorar")

        self._build_report_tab()
        self._build_explorer_tab()

    def _build_report_tab(self) -> None:
        container = ttk.Frame(self.report_tab)
        container.pack(fill=tk.BOTH, expand=True)

        columns = ("size", "owner", "confidence", "location", "folder", "files", "errors", "path")
        self.tree = ttk.Treeview(container, columns=columns, show="headings")
        headings = {
            "size": "Tamanho",
            "owner": "Origem provavel",
            "confidence": "Confianca",
            "location": "Area",
            "folder": "Pasta",
            "files": "Arquivos",
            "errors": "Erros",
            "path": "Caminho",
        }
        widths = {
            "size": 95,
            "owner": 180,
            "confidence": 90,
            "location": 80,
            "folder": 170,
            "files": 80,
            "errors": 65,
            "path": 440,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column], command=lambda col=column: self.sort_results(col))
            self.tree.column(column, width=widths[column], minwidth=60, anchor=tk.W)

        yscroll = ttk.Scrollbar(container, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=(0, 10))
        yscroll.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 10), pady=(0, 10))

        self.tree.bind("<<TreeviewSelect>>", self.show_details)
        self.tree.bind("<Double-1>", self.open_selected_result_in_explorer_tab)

        details = ttk.LabelFrame(self.report_tab, text="Detalhes", padding=10)
        details.pack(fill=tk.X, padx=10, pady=(0, 10))
        self.details_text = tk.StringVar(value="Selecione uma linha para ver o motivo da identificacao.")
        ttk.Label(details, textvariable=self.details_text, wraplength=1100, justify=tk.LEFT).pack(fill=tk.X)

    def _build_explorer_tab(self) -> None:
        actions = ttk.Frame(self.explorer_tab, padding=(0, 0, 0, 8))
        actions.pack(fill=tk.X)

        ttk.Button(actions, text="Abrir pasta do relatorio", command=self.open_selected_result_in_explorer_tab).pack(side=tk.LEFT)
        ttk.Button(actions, text="Abrir selecionado", command=self.open_selected_file).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(actions, text="Mostrar no Explorer", command=self.reveal_selected_file).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(actions, text="Excluir selecionado", command=self.delete_selected_file).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(actions, text="Atualizar", command=self.refresh_current_folder).pack(side=tk.LEFT, padx=(8, 0))

        columns = ("name", "kind", "size", "folders", "files", "modified", "path")
        self.file_tree = ttk.Treeview(self.explorer_tab, columns=columns, show="headings")
        headings = {
            "name": "Nome",
            "kind": "Tipo",
            "size": "Tamanho",
            "folders": "Subpastas",
            "files": "Arquivos",
            "modified": "Modificado",
            "path": "Caminho",
        }
        widths = {
            "name": 300,
            "kind": 90,
            "size": 100,
            "folders": 85,
            "files": 80,
            "modified": 150,
            "path": 500,
        }
        for column in columns:
            self.file_tree.heading(column, text=headings[column], command=lambda col=column: self.sort_files(col))
            self.file_tree.column(column, width=widths[column], minwidth=70, anchor=tk.W)

        yscroll = ttk.Scrollbar(self.explorer_tab, orient=tk.VERTICAL, command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=yscroll.set)
        self.file_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.file_tree.bind("<Double-1>", self.file_double_click)

    def start_scan(self) -> None:
        if self.worker and self.worker.is_alive():
            return

        self.results = []
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.scan_button.configure(state=tk.DISABLED)
        self.export_csv_button.configure(state=tk.DISABLED)
        self.export_json_button.configure(state=tk.DISABLED)
        self.progress.pack(side=tk.RIGHT)
        self.progress.configure(value=0, maximum=100)
        self.status.set("Iniciando analise...")

        min_size = self._parse_min_size()
        self.worker = threading.Thread(target=scan_appdata, args=(self.progress_queue, min_size), daemon=True)
        self.worker.start()

    def _parse_min_size(self) -> int:
        value = self.min_size.get()
        number_text, unit = value.split()
        number = int(number_text)
        multipliers = {"MB": 1024**2, "GB": 1024**3}
        return number * multipliers.get(unit, 1)

    def _poll_queue(self) -> None:
        try:
            while True:
                message = self.progress_queue.get_nowait()
                kind = message[0]
                if kind == "status":
                    self.status.set(message[1])
                elif kind == "progress":
                    processed, total = message[1], message[2]
                    self.progress.configure(maximum=total, value=processed)
                elif kind == "done":
                    self._load_results(message[1])
        except queue.Empty:
            pass
        self.after(150, self._poll_queue)

    def _load_results(self, results: list[ScanResult]) -> None:
        self.results = results
        self.render_results()

        self.scan_button.configure(state=tk.NORMAL)
        self.export_csv_button.configure(state=tk.NORMAL if results else tk.DISABLED)
        self.export_json_button.configure(state=tk.NORMAL if results else tk.DISABLED)
        self.progress.pack_forget()
        self.status.set(f"Analise concluida: {len(results)} pastas listadas.")

    def render_results(self) -> None:
        for item_id in self.tree.get_children():
            self.tree.delete(item_id)

        for index, item in enumerate(self.results):
            self.tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    human_size(item.size_bytes),
                    item.likely_owner,
                    item.confidence,
                    item.location,
                    item.folder_name,
                    item.file_count,
                    item.errors,
                    item.path,
                ),
            )

    def sort_results(self, column: str) -> None:
        reverse = self.sort_state.get(column, False)
        confidence_order = {"Alta": 3, "Media": 2, "Baixa": 1, "Nenhuma": 0}

        def key(item: ScanResult):
            if column == "size":
                return item.size_bytes
            if column == "files":
                return item.file_count
            if column == "errors":
                return item.errors
            if column == "confidence":
                return confidence_order.get(item.confidence, -1)
            if column == "owner":
                return item.likely_owner.lower()
            if column == "location":
                return item.location.lower()
            if column == "folder":
                return item.folder_name.lower()
            if column == "path":
                return item.path.lower()
            return ""

        self.results.sort(key=key, reverse=reverse)
        self.sort_state[column] = not reverse
        self.render_results()

    def show_details(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        item = self.results[int(selected[0])]
        self.details_text.set(
            f"{item.path}\n"
            f"Origem provavel: {item.likely_owner} | Confianca: {item.confidence}\n"
            f"Motivo: {item.reason}"
        )

    def selected_result(self) -> ScanResult | None:
        selected = self.tree.selection()
        if not selected:
            return None
        return self.results[int(selected[0])]

    def open_selected_result_in_explorer_tab(self, _event=None) -> None:
        item = self.selected_result()
        if item is None:
            messagebox.showinfo(APP_NAME, "Selecione uma pasta no relatorio primeiro.")
            return
        self.navigate_to(Path(item.path), remember=True)
        self.notebook.select(self.explorer_tab)

    def navigate_from_entry(self, _event=None) -> None:
        path = Path(self.path_var.get().strip())
        self.navigate_to(path, remember=True)

    def navigate_to(self, path: Path, remember: bool = True) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            messagebox.showerror(APP_NAME, "Caminho invalido ou inacessivel.")
            return

        if not resolved.exists() or not resolved.is_dir():
            messagebox.showerror(APP_NAME, "O caminho precisa ser uma pasta existente.")
            return

        if self.current_folder and remember and resolved != self.current_folder:
            self.folder_history.append(self.current_folder)

        self.current_folder = resolved
        self.path_var.set(str(resolved))
        self.load_folder_entries(resolved)

    def load_folder_entries(self, folder: Path) -> None:
        for item_id in self.file_tree.get_children():
            self.file_tree.delete(item_id)

        rows: list[tuple[str, str, int, int, int, float, str]] = []
        try:
            entries = list(os.scandir(folder))
        except (OSError, PermissionError) as exc:
            messagebox.showerror(APP_NAME, f"Nao foi possivel abrir a pasta:\n{exc}")
            return

        for entry in entries:
            try:
                if entry.is_symlink():
                    kind = "Atalho"
                    size = 0
                    folder_count = 0
                    file_count = 0
                elif entry.is_dir(follow_symlinks=False):
                    kind = "Pasta"
                    size, file_count, folder_count, _errors, _evidence = folder_size(Path(entry.path))
                else:
                    kind = "Arquivo"
                    size = entry.stat(follow_symlinks=False).st_size
                    folder_count = 0
                    file_count = 1
                modified = entry.stat(follow_symlinks=False).st_mtime
                rows.append((entry.name, kind, size, folder_count, file_count, modified, entry.path))
            except (OSError, PermissionError):
                rows.append((entry.name, "Bloqueado", 0, 0, 0, 0, entry.path))

        rows.sort(key=lambda row: (row[1] != "Pasta", row[0].lower()))
        for row in rows:
            self.insert_file_row(row)

        self.status.set(f"Explorando {folder} ({len(rows)} itens).")

    def insert_file_row(self, row: tuple[str, str, int, int, int, float, str]) -> None:
        name, kind, size, folder_count, file_count, modified, path = row
        modified_text = time.strftime("%Y-%m-%d %H:%M", time.localtime(modified)) if modified else "-"
        size_text = human_size(size)
        self.file_tree.insert("", tk.END, values=(name, kind, size_text, folder_count, file_count, modified_text, path))

    def sort_files(self, column: str) -> None:
        reverse = self.file_sort_state.get(column, False)
        rows = []
        for item_id in self.file_tree.get_children():
            values = self.file_tree.item(item_id, "values")
            rows.append(values)

        def size_value(value: str) -> int:
            if value == "-":
                return -1
            number, unit = value.split()
            multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
            return int(float(number) * multipliers.get(unit, 1))

        indexes = {"name": 0, "kind": 1, "size": 2, "folders": 3, "files": 4, "modified": 5, "path": 6}
        index = indexes[column]
        if column == "size":
            key = lambda row: size_value(row[index])
        elif column in ("folders", "files"):
            key = lambda row: int(row[index])
        else:
            key = lambda row: str(row[index]).lower()

        rows.sort(key=key, reverse=reverse)
        self.file_sort_state[column] = not reverse

        for item_id in self.file_tree.get_children():
            self.file_tree.delete(item_id)
        for row in rows:
            self.file_tree.insert("", tk.END, values=row)

    def selected_file_path(self) -> Path | None:
        selected = self.file_tree.selection()
        if not selected:
            return None
        values = self.file_tree.item(selected[0], "values")
        return Path(values[6])

    def file_double_click(self, _event=None) -> None:
        path = self.selected_file_path()
        if path is None:
            return
        if path.is_dir():
            self.navigate_to(path, remember=True)
        else:
            self.open_path(path)

    def open_selected_file(self) -> None:
        path = self.selected_file_path()
        if path is None:
            messagebox.showinfo(APP_NAME, "Selecione um item no explorador interno.")
            return
        self.open_path(path)

    def open_path(self, path: Path) -> None:
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Nao foi possivel abrir:\n{exc}")

    def reveal_selected_file(self) -> None:
        path = self.selected_file_path()
        if path is None:
            messagebox.showinfo(APP_NAME, "Selecione um item no explorador interno.")
            return
        try:
            if path.is_dir():
                subprocess.Popen(["explorer", str(path)])
            else:
                subprocess.Popen(["explorer", "/select,", str(path)])
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Nao foi possivel abrir no Explorer:\n{exc}")

    def delete_selected_file(self) -> None:
        path = self.selected_file_path()
        if path is None:
            messagebox.showinfo(APP_NAME, "Selecione um item para excluir.")
            return

        if not self.is_inside_appdata(path):
            messagebox.showerror(APP_NAME, "Por seguranca, a exclusao so e permitida dentro do AppData analisado.")
            return

        confirm = messagebox.askyesno(
            APP_NAME,
            f"Excluir permanentemente este item?\n\n{path}\n\nEssa acao nao envia para a lixeira.",
        )
        if not confirm:
            return

        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Nao foi possivel excluir:\n{exc}")
            return

        self.refresh_current_folder()
        self.status.set(f"Item excluido: {path}")

    def is_inside_appdata(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
        except OSError:
            return False
        return any(resolved == root or root in resolved.parents for root in self.appdata_root_paths)

    def refresh_current_folder(self) -> None:
        if self.current_folder is not None:
            self.load_folder_entries(self.current_folder)

    def go_back(self) -> None:
        if not self.folder_history:
            return
        previous = self.folder_history.pop()
        self.navigate_to(previous, remember=False)

    def go_up(self) -> None:
        if self.current_folder is None:
            return
        parent = self.current_folder.parent
        if parent == self.current_folder:
            return
        self.navigate_to(parent, remember=True)

    def export_csv(self) -> None:
        if not self.results:
            return
        path = filedialog.asksaveasfilename(
            title="Exportar CSV",
            defaultextension=".csv",
            initialfile=f"adata_explorer_{int(time.time())}.csv",
            filetypes=(("CSV", "*.csv"),),
        )
        if not path:
            return

        with open(path, "w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=list(asdict(self.results[0]).keys()) + ["size"])
            writer.writeheader()
            for item in self.results:
                row = asdict(item)
                row["size"] = human_size(item.size_bytes)
                writer.writerow(row)
        messagebox.showinfo(APP_NAME, "CSV exportado com sucesso.")

    def export_json(self) -> None:
        if not self.results:
            return
        path = filedialog.asksaveasfilename(
            title="Exportar JSON",
            defaultextension=".json",
            initialfile=f"adata_explorer_{int(time.time())}.json",
            filetypes=(("JSON", "*.json"),),
        )
        if not path:
            return

        with open(path, "w", encoding="utf-8") as file:
            json.dump([asdict(item) for item in self.results], file, indent=2, ensure_ascii=False)
        messagebox.showinfo(APP_NAME, "JSON exportado com sucesso.")


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
