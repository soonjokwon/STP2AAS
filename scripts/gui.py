"""Simple desktop GUI for stp2aas — pick a CAD file, convert to .aasx, and view.

Runs with the project's conda env python (Tkinter ships with it):

    python scripts/gui.py

or double-click run_gui.bat. A thin GUI over the same pipeline the CLI uses.
"""

from __future__ import annotations

import pathlib
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def _open_file(path: str) -> None:
    """Open a local file with its default app (delegates to the viewer's helper)."""
    from stp2aas.viewer import open_in_browser

    open_in_browser(path)


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("stp2aas — STEP / AP242 → AAS")
        root.geometry("760x520")
        self.q: queue.Queue = queue.Queue()
        self.last_html: str | None = None

        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(root)
        frm.pack(fill="x", **pad)

        ttk.Label(frm, text="입력 (STEP/.stp/.step 또는 AP242 .stpx/.xml)").grid(
            row=0, column=0, sticky="w"
        )
        self.in_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.in_var, width=70).grid(row=1, column=0, sticky="we")
        ttk.Button(frm, text="찾아보기…", command=self.browse_in).grid(row=1, column=1, padx=4)

        ttk.Label(frm, text="출력 .aasx").grid(row=2, column=0, sticky="w")
        self.out_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.out_var, width=70).grid(row=3, column=0, sticky="we")
        ttk.Button(frm, text="저장 위치…", command=self.browse_out).grid(row=3, column=1, padx=4)
        frm.columnconfigure(0, weight=1)

        opts = ttk.Frame(root)
        opts.pack(fill="x", **pad)
        self.link_only = tk.BooleanVar(value=False)
        self.nameplate = tk.BooleanVar(value=False)
        self.build_viewer = tk.BooleanVar(value=True)
        self.open_viewer = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="--link-only (임베드 안 함)", variable=self.link_only).grid(
            row=0, column=0, sticky="w", padx=4
        )
        ttk.Checkbutton(opts, text="부분 Nameplate 포함", variable=self.nameplate).grid(
            row=0, column=1, sticky="w", padx=4
        )
        ttk.Checkbutton(opts, text="3D 뷰어(HTML) 생성", variable=self.build_viewer).grid(
            row=0, column=2, sticky="w", padx=4
        )
        ttk.Checkbutton(opts, text="완료 후 뷰어 열기", variable=self.open_viewer).grid(
            row=0, column=3, sticky="w", padx=4
        )

        ttk.Label(opts, text="AAS 구조:").grid(row=1, column=0, sticky="e", padx=4, pady=(6, 0))
        self.structure = tk.StringVar(value="hierarchical")
        ttk.Combobox(
            opts,
            textvariable=self.structure,
            width=14,
            state="readonly",
            values=("hierarchical", "flat", "single"),
        ).grid(row=1, column=1, sticky="w", pady=(6, 0))

        btns = ttk.Frame(root)
        btns.pack(fill="x", **pad)
        self.convert_btn = ttk.Button(btns, text="변환 실행", command=self.start)
        self.convert_btn.pack(side="left")
        self.view_btn = ttk.Button(btns, text="뷰어 열기", command=self.open_last, state="disabled")
        self.view_btn.pack(side="left", padx=6)
        self.status = ttk.Label(btns, text="대기 중")
        self.status.pack(side="left", padx=10)

        self.log = scrolledtext.ScrolledText(root, height=20, font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=8, pady=6)

        self._last_suggested = ""  # output we auto-filled; lets us follow input changes
        self.in_var.trace_add("write", lambda *_: self._suggest_out())
        self.root.after(100, self._drain)

    # --- UI actions ---------------------------------------------------------

    def browse_in(self) -> None:
        f = filedialog.askopenfilename(
            title="입력 CAD 파일 선택",
            filetypes=[("CAD", "*.stp *.step *.stpx *.xml"), ("모든 파일", "*.*")],
        )
        if f:
            self.in_var.set(f)

    def browse_out(self) -> None:
        f = filedialog.asksaveasfilename(defaultextension=".aasx", filetypes=[("AASX", "*.aasx")])
        if f:
            self.out_var.set(f)  # user's explicit choice — no longer auto-followed
            self._last_suggested = ""

    def _suggest_out(self) -> None:
        """Keep the output following the input, unless the user set it themselves."""
        p = self.in_var.get().strip()
        if not p:
            return
        suggestion = str(ROOT / "out" / (pathlib.Path(p).stem + ".aasx"))
        current = self.out_var.get().strip()
        if current == "" or current == self._last_suggested:
            self.out_var.set(suggestion)
            self._last_suggested = suggestion

    def start(self) -> None:
        inp = self.in_var.get().strip()
        out = self.out_var.get().strip() or str(ROOT / "out" / (pathlib.Path(inp).stem + ".aasx"))
        self.out_var.set(out)
        if not inp or not pathlib.Path(inp).is_file():
            self._log("⚠ 입력 파일을 선택하세요.\n")
            return
        self.convert_btn.config(state="disabled")
        self.view_btn.config(state="disabled")
        self.status.config(text="변환 중…")
        self.log.delete("1.0", "end")
        threading.Thread(target=self._worker, args=(inp, out), daemon=True).start()

    def open_last(self) -> None:
        if self.last_html:
            _open_file(self.last_html)

    # --- worker thread ------------------------------------------------------

    def _worker(self, inp: str, out: str) -> None:
        try:
            from stp2aas.aasx_writer import write_aasx
            from stp2aas.extract import extract_any

            self.q.put(("log", f"입력: {inp}\n변환 시작…\n"))
            doc = extract_any(inp)
            pathlib.Path(out).parent.mkdir(parents=True, exist_ok=True)
            write_aasx(
                doc,
                out,
                embed=not self.link_only.get(),
                include_partial_nameplate=self.nameplate.get(),
                assembly_structure=self.structure.get(),
            )
            self.q.put(("log", f"✓ .aasx 생성: {out}\n"))

            html = None
            if self.build_viewer.get():
                self.q.put(("log", "3D 뷰어 생성 중… (부품 테셀레이션)\n"))
                from stp2aas import viewer

                html, n = viewer.render(out, enable_3d=True)
                self.q.put(("log", f"✓ 뷰어 생성: {html}  (3D 메시 {n}개)\n"))

            self.q.put(("done", html))
        except Exception:  # noqa: BLE001
            import traceback

            self.q.put(("log", "✗ 오류:\n" + traceback.format_exc()))
            self.q.put(("done", None))

    # --- queue pump ---------------------------------------------------------

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "done":
                    self.convert_btn.config(state="normal")
                    self.status.config(text="완료")
                    if payload:
                        self.last_html = payload
                        self.view_btn.config(state="normal")
                        if self.open_viewer.get():
                            self.open_last()
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _log(self, text: str) -> None:
        self.log.insert("end", text)
        self.log.see("end")


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
