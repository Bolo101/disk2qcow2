"""
admin_interface.py – Password-protected administration panel for the P2V Converter.

Features:
• Conversion counter (total virtualised machines)
• PDF export: session report / complete logs → external storage
• Raw log export (all rotated files) → external storage
• Log purge
• Admin password change
• Power off / Reboot
• Exit to OS
"""

import json
import os
import shutil
import subprocess
import tempfile
import tkinter as tk
from tkinter import filedialog, ttk
import theme
from datetime import datetime
from typing import List

from config_manager import (
    change_password,
    is_password_set,
    set_password,
    verify_password_with_wait,
)
from log_handler import (
    generate_log_file_pdf,
    generate_session_pdf,
    get_all_log_files,
    is_session_active,
    log_application_exit,
    log_error,
    log_info,
    purge_logs,
    session_end,
)
from stats_manager import get_conversion_count


# ── Password dialog ────────────────────────────────────────────────────────────

class PasswordDialog(tk.Toplevel):
    """Modal password entry dialog."""

    def __init__(self, parent: tk.Widget, title: str = "Authentification") -> None:
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        theme.apply_theme(self)

        self.result: str | None = None

        ttk.Label(
            self,
            text="Mot de passe administrateur :",
            font=("Arial", 11),
        ).pack(padx=20, pady=(16, 4))
        self._entry = ttk.Entry(self, show="•", width=28, font=("Arial", 11))
        self._entry.pack(padx=20, pady=4)
        self._entry.bind("<Return>", lambda _: self._ok())
        self._entry.focus_set()

        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="OK", command=self._ok).pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_frame, text="Annuler", command=self._cancel).pack(side=tk.LEFT, padx=6)

        self._center(parent)
        self.wait_window()

    def _center(self, parent: tk.Widget) -> None:
        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{px}+{py}")

    def _ok(self) -> None:
        self.result = self._entry.get()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


# ── First-run password setup ───────────────────────────────────────────────────

def prompt_initial_password(parent: tk.Widget) -> None:
    """
    Displayed on first launch: forces creation of the admin password.
    Loops until a valid password is set.
    """
    while True:
        win = tk.Toplevel(parent)
        win.title("Configuration initiale — Mot de passe administrateur")
        win.resizable(False, False)
        win.grab_set()
        win.protocol("WM_DELETE_WINDOW", lambda: None)
        theme.apply_theme(win)

        ttk.Label(
            win,
            text="Définir le mot de passe administrateur.",
            font=("Arial", 11, "bold"),
        ).pack(padx=20, pady=(14, 6))
        ttk.Label(
            win,
            text="Ce mot de passe protège le panneau d'administration\n"
                 "(export des journaux, arrêt système, etc.)",
            justify=tk.LEFT,
        ).pack(padx=20)

        fields: dict[str, ttk.Entry] = {}
        for label in ("Mot de passe :", "Confirmation :"):
            ttk.Label(win, text=label).pack(anchor="w", padx=20, pady=(6, 0))
            entry = ttk.Entry(win, show="•", width=28)
            entry.pack(padx=20, pady=2)
            fields[label] = entry

        err_var = tk.StringVar()
        ttk.Label(win, textvariable=err_var, foreground="red").pack(pady=2)

        submitted: list[bool] = [False]

        def on_submit() -> None:
            password = fields["Mot de passe :"].get()
            password_confirm = fields["Confirmation :"].get()

            if len(password) < 8:
                err_var.set("Le mot de passe doit comporter au moins 8 caractères.")
                return
            if password != password_confirm:
                err_var.set("Les mots de passe ne correspondent pas.")
                return
            try:
                set_password(password)
                submitted[0] = True
                win.destroy()
            except Exception as exc:
                err_var.set(f"Error: {exc}")

        ttk.Button(win, text="Définir le mot de passe", command=on_submit).pack(pady=10)
        win.wait_window()

        if submitted[0]:
            log_info("Mot de passe administrateur défini avec succès.")
            break


# ── External-storage helpers ───────────────────────────────────────────────────

def _get_external_disks() -> list:
    """
    Return non-system, non-loop block devices visible to lsblk.
    Each entry: {device, path, size, model, partitions, mount_points}.
    """
    result = []
    try:
        raw = subprocess.run(
            ["lsblk", "-J", "-o", "NAME,SIZE,TYPE,MODEL,MOUNTPOINT"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout.decode()
        data = json.loads(raw)
    except Exception as exc:
        log_error(f"lsblk JSON failed: {exc}")
        return result

    try:
        from utils import is_system_disk as _isd
        _isd_fn = _isd
    except ImportError:
        _isd_fn = None

    for dev in data.get("blockdevices", []):
        dev_name = dev.get("name", "")
        dev_type = dev.get("type", "")
        if dev_type != "disk":
            continue
        if dev_name.startswith("loop"):
            continue
        if _isd_fn and _isd_fn(f"/dev/{dev_name}"):
            continue

        partitions: List[str] = []
        mount_map: dict = {}
        for child in (dev.get("children") or []):
            if child.get("type") == "part":
                part_name = child["name"]
                partitions.append(part_name)
                mount_map[part_name] = child.get("mountpoint") or None

        if not partitions:
            partitions.append(dev_name)
            mount_map[dev_name] = dev.get("mountpoint") or None

        result.append(
            {
                "device": dev_name,
                "path": f"/dev/{dev_name}",
                "size": dev.get("size", "?"),
                "model": (dev.get("model") or "").strip(),
                "partitions": partitions,
                "mount_points": mount_map,
            }
        )

    return result


def _mount_partition(partition: str) -> str | None:
    """Mount /dev/<partition> to a temp dir. Returns mount point or None."""
    mount_dir = tempfile.mkdtemp(prefix="p2v_admin_export_")
    try:
        result = subprocess.run(
            ["mount", f"/dev/{partition}", mount_dir],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            log_error(
                f"mount /dev/{partition} -> {mount_dir} failed: "
                f"{result.stderr.decode().strip()}"
            )
            try:
                os.rmdir(mount_dir)
            except OSError:
                pass
            return None

        log_info(f"Mounted /dev/{partition} at {mount_dir}")
        return mount_dir
    except Exception as exc:
        log_error(f"Unexpected error mounting /dev/{partition}: {exc}")
        return None


def _unmount_partition(mount_dir: str) -> None:
    """Unmount and remove the temp mount directory."""
    try:
        result = subprocess.run(
            ["umount", mount_dir],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            log_error(f"umount {mount_dir} failed: {result.stderr.decode().strip()}")
        else:
            log_info(f"Unmounted {mount_dir}")
    except Exception as exc:
        log_error(f"Error during umount {mount_dir}: {exc}")
    finally:
        try:
            os.rmdir(mount_dir)
        except OSError:
            pass


def _show_disk_picker(parent: tk.Widget, external_disks: list):
    """
    Modal dialog to pick one partition.
    Returns (partition_name, already_mounted, existing_mount_point)
    or (None, False, None) if cancelled.
    """
    result = {"partition": None, "already_mounted": False, "mount_point": None}

    dlg = tk.Toplevel(parent)
    dlg.title("Sélectionner le support externe")
    dlg.grab_set()
    dlg.resizable(False, False)
    theme.apply_theme(dlg)

    ttk.Label(
        dlg,
        text="Choisissez le support externe pour l'export",
        font=("Arial", 11, "bold"),
        padding=(10, 10),
    ).pack(fill=tk.X)
    ttk.Label(
        dlg,
        text="Seuls les disques non-système sont listés.\n"
             "Le périphérique sera monté automatiquement si nécessaire.",
        foreground="#555555",
        padding=(10, 0, 10, 6),
    ).pack(fill=tk.X)

    frame = ttk.Frame(dlg, padding=(10, 0, 10, 6))
    frame.pack(fill=tk.BOTH, expand=True)

    lb = tk.Listbox(
        frame,
        width=70,
        height=12,
        font=("Courier", 9),
        selectmode=tk.SINGLE,
        activestyle="dotbox",
    )
    sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=lb.yview)
    lb.configure(yscrollcommand=sb.set)
    lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    sb.pack(side=tk.RIGHT, fill=tk.Y)

    entries = []
    for disk in external_disks:
        model_str = f" [{disk['model']}]" if disk["model"] else ""
        lb.insert(tk.END, f"── {disk['path']} {disk['size']}{model_str}")
        lb.itemconfig(tk.END, foreground="#333388", background="#eeeeff")
        entries.append(None)

        for part in disk["partitions"]:
            mount_point = disk["mount_points"].get(part)
            status = f"monté sur {mount_point}" if mount_point else "non monté"
            lb.insert(tk.END, f" /dev/{part:<14} {status}")
            entries.append((part, mount_point is not None, mount_point))

    btn_frame = ttk.Frame(dlg, padding=(10, 6))
    btn_frame.pack(fill=tk.X)

    warn_lbl = tk.Label(
        btn_frame,
        text="",
        bg=theme.BG,
        fg=theme.WARNING,
        font=theme.FONT_SMALL,
    )
    warn_lbl.pack(side=tk.LEFT, padx=(6, 0))

    def on_select() -> None:
        sel = lb.curselection()
        if not sel:
            warn_lbl.config(text="⚠ Veuillez sélectionner une partition.")
            return

        entry = entries[sel[0]]
        if entry is None:
            warn_lbl.config(text="⚠ Sélectionnez une partition, pas un en-tête de disque.")
            return

        warn_lbl.config(text="")
        result.update(
            {
                "partition": entry[0],
                "already_mounted": entry[1],
                "mount_point": entry[2],
            }
        )
        dlg.destroy()

    ttk.Button(
        btn_frame,
        text="Sélectionner",
        style="Primary.TButton",
        command=on_select,
    ).pack(side=tk.LEFT, padx=4)
    ttk.Button(btn_frame, text="Annuler", command=dlg.destroy).pack(side=tk.LEFT, padx=4)

    dlg.update_idletasks()
    width, height = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
    x_pos = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
    y_pos = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
    dlg.geometry(f"+{x_pos}+{y_pos}")
    parent.wait_window(dlg)

    return result["partition"], result["already_mounted"], result["mount_point"]


def _request_external_export_path(
    parent: tk.Widget,
    default_filename: str,
    status_callback=None,
) -> tuple[str | None, str | None]:
    """
    Full export-path workflow: detect → pick → mount → save-as dialog.

    Returns (chosen_path, mount_dir_to_unmount_after_write)
    or (None, None) if cancelled / error.
    The caller must call _unmount_partition(mount_dir) after the file is written.
    """
    def _status(msg: str) -> None:
        if status_callback:
            status_callback(msg)

    external_disks = _get_external_disks()
    if not external_disks:
        _show_dark_error(
            parent,
            "Aucun support externe détecté",
            "Aucun disque externe n'a été trouvé.\n\n"
            "Connectez une clé USB ou un disque externe et réessayez.",
        )
        return None, None

    partition, already_mounted, existing_mp = _show_disk_picker(parent, external_disks)
    if not partition:
        return None, None

    pending_unmount = None
    if already_mounted and existing_mp:
        mount_point = existing_mp
    else:
        _status(f"Montage de /dev/{partition}…")
        mount_point = _mount_partition(partition)
        if not mount_point:
            _show_dark_error(
                parent,
                "Erreur de montage",
                f"Impossible de monter /dev/{partition}.\n\n"
                "Vérifiez que le périphérique est correctement connecté.",
            )
            return None, None
        pending_unmount = mount_point

    chosen_path = filedialog.asksaveasfilename(
        title="Exporter vers le support externe",
        initialdir=mount_point,
        initialfile=default_filename,
        defaultextension=os.path.splitext(default_filename)[1] or "",
        filetypes=[("Tous les fichiers", "*.*")],
        parent=parent,
    )

    if not chosen_path:
        if pending_unmount:
            _unmount_partition(pending_unmount)
        return None, None

    mp_norm = mount_point.rstrip("/") + "/"
    path_norm = os.path.abspath(chosen_path).rstrip("/") + "/"
    if not path_norm.startswith(mp_norm):
        _show_dark_warning(
            parent,
            "Destination invalide",
            f"Le chemin choisi n'est pas sur le support externe.\n"
            f"Choisissez un emplacement sous : {mount_point}",
        )
        if pending_unmount:
            _unmount_partition(pending_unmount)
        return None, None

    return chosen_path, pending_unmount


class LogFileSelectionDialog(tk.Toplevel):
    """
    Modal dialog that lists all available log files with individual checkboxes
    and Select All / Deselect All convenience buttons.
    """

    def __init__(self, parent: tk.Widget, log_files: List[str]) -> None:
        super().__init__(parent)
        self.title("Sélectionner les fichiers journaux à exporter")
        self.resizable(False, False)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        theme.apply_theme(self)

        self.selected_files: List[str] | None = None
        self._log_files = log_files
        self._vars: List[tk.BooleanVar] = []

        self._build_ui()
        self._center(parent)
        self.wait_window()

    def _build_ui(self) -> None:
        ttk.Label(
            self,
            text="Choisissez les fichiers à copier sur le support externe :",
            font=("Arial", 10, "bold"),
            padding=(12, 10, 12, 4),
        ).pack(fill=tk.X)

        ctrl_frame = ttk.Frame(self, padding=(12, 0, 12, 6))
        ctrl_frame.pack(fill=tk.X)
        ttk.Button(
            ctrl_frame,
            text="Tout sélectionner",
            command=self._select_all,
            width=18,
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            ctrl_frame,
            text="Tout désélectionner",
            command=self._deselect_all,
            width=18,
        ).pack(side=tk.LEFT)

        list_outer = ttk.Frame(self, padding=(10, 0, 10, 6))
        list_outer.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(
            list_outer,
            width=520,
            height=min(240, len(self._log_files) * 28 + 10),
            highlightthickness=0,
            bg=theme.BG_CARD,
        )
        sb = ttk.Scrollbar(list_outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        inner = ttk.Frame(canvas)
        canvas_window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_configure(_event) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(canvas_window, width=canvas.winfo_width())

        inner.bind("<Configure>", _on_configure)
        canvas.bind("<Configure>", _on_configure)

        def _on_mousewheel(event) -> None:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        self.protocol(
            "WM_DELETE_WINDOW",
            lambda: (canvas.unbind_all("<MouseWheel>"), self._cancel()),
        )

        for path in self._log_files:
            var = tk.BooleanVar(value=True)
            self._vars.append(var)

            row = ttk.Frame(inner)
            row.pack(fill=tk.X, padx=4, pady=1)

            ttk.Checkbutton(row, variable=var).pack(side=tk.LEFT)

            name = os.path.basename(path)
            try:
                stat = os.stat(path)
                size = _human_size(stat.st_size)
                mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                label = f"{name:<40} {size:>8} {mtime}"
            except OSError:
                label = f"{name} (illisible)"

            ttk.Label(row, text=label, font=("Courier", 9)).pack(side=tk.LEFT, padx=(4, 0))

        self._count_var = tk.StringVar()
        self._update_count()
        for var in self._vars:
            var.trace_add("write", lambda *_: self._update_count())

        ttk.Label(
            self,
            textvariable=self._count_var,
            foreground="#555555",
            padding=(12, 0, 12, 4),
        ).pack(fill=tk.X)

        ttk.Separator(self).pack(fill=tk.X, padx=10, pady=4)
        self._warn_lbl = tk.Label(
            self,
            text="",
            bg=theme.BG,
            fg=theme.WARNING,
            font=theme.FONT_SMALL,
        )
        self._warn_lbl.pack(anchor="w", padx=12)

        btn_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        btn_frame.pack(fill=tk.X)
        ttk.Button(
            btn_frame,
            text="Exporter la sélection",
            style="Primary.TButton",
            command=self._confirm,
            width=18,
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            btn_frame,
            text="Annuler",
            command=self._cancel,
            width=12,
        ).pack(side=tk.LEFT)

    def _select_all(self) -> None:
        for var in self._vars:
            var.set(True)

    def _deselect_all(self) -> None:
        for var in self._vars:
            var.set(False)

    def _update_count(self) -> None:
        count = sum(var.get() for var in self._vars)
        self._count_var.set(f"{count} sur {len(self._vars)} fichier(s) sélectionné(s)")

    def _confirm(self) -> None:
        chosen = [path for path, var in zip(self._log_files, self._vars) if var.get()]
        if not chosen:
            self._warn_lbl.config(text="⚠ Sélectionnez au moins un fichier.")
            return
        self.selected_files = chosen
        self.destroy()

    def _cancel(self) -> None:
        self.selected_files = None
        self.destroy()

    def _center(self, parent: tk.Widget) -> None:
        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{px}+{py}")


def _human_size(n: int) -> str:
    """Convert bytes to a short human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ── Administration panel ───────────────────────────────────────────────────────

class AdminInterface(tk.Toplevel):
    """Full administration window, opened after successful authentication."""

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent)
        self._parent = parent
        self.title("Administration — Convertisseur P2V")
        self.attributes("-fullscreen", True)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        theme.apply_theme(self)

        self._status_var = tk.StringVar(value="Prêt")
        self._build_ui()
        self._refresh_stats()

    def _build_ui(self) -> None:
        C = theme

        self.configure(bg=C.BG)

        main_frame = ttk.Frame(self, style="TFrame", padding=(20, 16))
        main_frame.pack(fill="both", expand=True)

        bottom_frame = ttk.Frame(main_frame, style="TFrame")
        bottom_frame.pack(side="bottom", fill="x")

        notif_container = ttk.Frame(bottom_frame)
        notif_container.pack(fill=tk.X)
        notif_container.grid_columnconfigure(0, weight=1)
        self.notif_bar = theme.NotificationBar(notif_container)

        ttk.Separator(bottom_frame, orient="horizontal").pack(fill="x", pady=(8, 4))

        status_bar = ttk.Frame(bottom_frame, style="TFrame")
        status_bar.pack(fill="x", pady=(0, 6))
        ttk.Label(
            status_bar,
            text="Statut :",
            font=C.FONT_NORMAL,
            style="Card.TLabel",
        ).pack(side="left")
        ttk.Label(
            status_bar,
            textvariable=self._status_var,
            foreground=C.SUCCESS,
            font=C.FONT_NORMAL,
        ).pack(side="left", padx=6)
        ttk.Button(
            bottom_frame,
            text="Fermer le panneau",
            command=self.destroy,
            style="Primary.TButton",
            width=20,
        ).pack(pady=(0, 8))

        header_frame = ttk.Frame(main_frame, style="TFrame")
        header_frame.pack(fill="x", pady=(0, 18))

        ttk.Label(
            header_frame,
            text="Panneau d'administration",
            style="Title.TLabel",
        ).pack(anchor="center")
        ttk.Label(
            header_frame,
            text="Convertisseur P2V — Accès administrateur",
            style="Subtitle.TLabel",
        ).pack(anchor="center", pady=(2, 0))

        ttk.Separator(main_frame, orient="horizontal").pack(fill="x", pady=(0, 16))

        stats_frame = ttk.LabelFrame(
            main_frame,
            text="Statistiques",
            style="TLabelframe",
        )
        stats_frame.pack(fill="x", pady=(0, 12))

        stats_inner = ttk.Frame(stats_frame, style="TFrame")
        stats_inner.pack(anchor="w")

        self._count_var = tk.StringVar(value="—")
        ttk.Label(
            stats_inner,
            text="Machines virtualisées (total) :",
            font=C.FONT_NORMAL,
            style="Card.TLabel",
        ).pack(side="left")
        ttk.Label(
            stats_inner,
            textvariable=self._count_var,
            font=("Segoe UI", 22, "bold"),
            foreground=C.SUCCESS,
        ).pack(side="left", padx=12)
        ttk.Button(
            stats_inner,
            text="↻ Actualiser",
            command=self._refresh_stats,
        ).pack(side="left", padx=8)

    def _refresh_stats(self) -> None:
        self._count_var.set(str(get_conversion_count()))

    def _set_status(self, message: str) -> None:
        self._status_var.set(message)
        self.update_idletasks()

    def _notify(
        self,
        message: str,
        level: str = "info",
        confirm: bool = False,
        on_yes=None,
        on_no=None,
    ) -> None:
        try:
            self.notif_bar.show(
                message=message,
                level=level,
                confirm=confirm,
                on_yes=on_yes,
                on_no=on_no,
            )
        except Exception:
            self._status_var.set(message)

    def _change_password(self) -> None:
        win = tk.Toplevel(self)
        win.title("Changer le mot de passe")
        win.resizable(False, False)
        win.grab_set()

        fields: dict[str, ttk.Entry] = {}
        for label in (
            "Mot de passe actuel :",
            "Nouveau mot de passe :",
            "Confirmer le nouveau :",
        ):
            ttk.Label(win, text=label).pack(anchor="w", padx=20, pady=(8, 0))
            entry = ttk.Entry(win, show="•", width=26)
            entry.pack(padx=20, pady=2)
            fields[label] = entry

        err_var = tk.StringVar()
        ttk.Label(win, textvariable=err_var, foreground="red").pack(pady=2)

        def submit() -> None:
            old_password = fields["Mot de passe actuel :"].get()
            new_password = fields["Nouveau mot de passe :"].get()
            confirm_password = fields["Confirmer le nouveau :"].get()

            if len(new_password) < 8:
                err_var.set("Le nouveau mot de passe doit comporter au moins 8 caractères.")
                return
            if new_password != confirm_password:
                err_var.set("Les nouveaux mots de passe ne correspondent pas.")
                return

            try:
                change_password(old_password, new_password)
                win.destroy()
                self._notify("Mot de passe modifié avec succès.", level="success")
                log_info("Mot de passe administrateur modifié.")
            except ValueError as exc:
                err_var.set(str(exc))

        ttk.Button(win, text="Confirmer", command=submit).pack(pady=10)

    def _purge_logs(self) -> None:
        self._notify(
            "Supprimer TOUS les fichiers journaux ? Action irréversible.",
            level="warning",
            confirm=True,
            on_yes=self._do_purge_logs,
            on_no=None,
        )

    def _do_purge_logs(self) -> None:
        purge_logs()
        self._notify("Tous les fichiers journaux ont été supprimés.", level="success")

    def _shutdown(self) -> None:
        self._notify(
            "Éteindre le système maintenant ?",
            level="warning",
            confirm=True,
            on_yes=self._do_shutdown,
            on_no=None,
        )

    def _do_shutdown(self) -> None:
        log_application_exit("System shutdown via admin panel")
        try:
            subprocess.run(["systemctl", "poweroff"], check=False)
        except FileNotFoundError:
            try:
                subprocess.run(["shutdown", "-h", "now"], check=False)
            except FileNotFoundError:
                subprocess.run(["poweroff"], check=False)

    def _reboot(self) -> None:
        self._notify(
            "Redémarrer le système maintenant ?",
            level="warning",
            confirm=True,
            on_yes=self._do_reboot,
            on_no=None,
        )

    def _do_reboot(self) -> None:
        log_application_exit("System reboot via admin panel")
        try:
            subprocess.run(["reboot"], check=False)
        except FileNotFoundError:
            subprocess.run(["shutdown", "-r", "now"], check=False)

    def _exit_to_os(self) -> None:
        self._notify(
            "Fermer le convertisseur P2V et retourner à l'OS ?",
            level="warning",
            confirm=True,
            on_yes=self._do_exit_to_os,
            on_no=None,
        )

    def _do_exit_to_os(self) -> None:
        log_application_exit("Exit to OS via admin panel")
        try:
            if is_session_active():
                session_end()
        except Exception:
            pass
        self._parent.quit()
        self._parent.destroy()


def _show_dark_error(parent, title: str, message: str) -> None:
    """Affiche une erreur dans un Toplevel sombre."""
    _show_dark_dialog(parent, title, message, level="error")


def _show_dark_warning(parent, title: str, message: str) -> None:
    """Affiche un avertissement dans un Toplevel sombre."""
    _show_dark_dialog(parent, title, message, level="warning")


def _show_dark_dialog(parent, title: str, message: str, level: str = "info") -> None:
    import theme as _theme

    icons = {"error": "✖", "warning": "⚠", "info": "ℹ", "success": "✔"}
    fg_map = {
        "error": _theme.ERROR,
        "warning": _theme.WARNING,
        "info": _theme.INFO,
        "success": _theme.SUCCESS,
    }

    win = tk.Toplevel(parent)
    win.title(title)
    win.resizable(False, False)
    win.grab_set()
    _theme.apply_theme(win)

    frm = tk.Frame(win, bg=_theme.BG_CARD, padx=24, pady=20)
    frm.pack(fill="both", expand=True)

    hdr = tk.Frame(frm, bg=_theme.BG_CARD)
    hdr.pack(fill="x", pady=(0, 12))

    tk.Label(
        hdr,
        text=icons.get(level, "ℹ"),
        fg=fg_map.get(level, _theme.TEXT_PRIMARY),
        bg=_theme.BG_CARD,
        font=("Segoe UI", 20),
    ).pack(side="left", padx=(0, 10))
    tk.Label(
        hdr,
        text=title,
        fg=_theme.TEXT_PRIMARY,
        bg=_theme.BG_CARD,
        font=_theme.FONT_SUBTITLE,
    ).pack(side="left")

    tk.Label(
        frm,
        text=message,
        fg=_theme.TEXT_SECONDARY,
        bg=_theme.BG_CARD,
        font=_theme.FONT_NORMAL,
        wraplength=380,
        justify="left",
    ).pack(anchor="w", pady=(0, 16))

    tk.Button(
        frm,
        text="OK",
        bg=_theme.ACCENT,
        fg="#ffffff",
        activebackground=_theme.ACCENT_DARK,
        relief="flat",
        padx=20,
        pady=6,
        font=_theme.FONT_BTN_PRIMARY,
        cursor="hand2",
        bd=0,
        command=win.destroy,
    ).pack()

    win.update_idletasks()
    px = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_width()) // 2
    py = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_height()) // 2
    win.geometry(f"+{px}+{py}")
    win.wait_window()


def open_admin_panel(parent: tk.Widget) -> None:
    """
    Verify authentication then open the admin panel.
    Handles first-launch password setup automatically.
    """
    if not is_password_set():
        prompt_initial_password(parent)

    dlg = PasswordDialog(parent, title="Accès administration")
    if dlg.result is None:
        return

    ok, wait = verify_password_with_wait(dlg.result)

    if wait > 0:
        _show_dark_error(
            parent,
            "Accès temporairement verrouillé",
            f"Trop de tentatives. Réessayez dans {wait} seconde(s).",
        )
        log_error(
            f"Tentative de connexion admin refusée : verrouillage temporaire ({wait}s restantes)."
        )
        return

    if not ok:
        _show_dark_error(parent, "Accès refusé", "Mot de passe incorrect.")
        log_error("Tentative de connexion admin échouée (mot de passe incorrect).")
        return

    log_info("Accès au panneau d'administration accordé.")
    AdminInterface(parent)