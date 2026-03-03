import os
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from openmind_hub import snapshot_download

def set_env_var(local_dir: str):
    os.environ["HUB_WHITE_LIST_PATHS"] = local_dir

def is_dir_writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        testfile = os.path.join(path, ".write_test.tmp")
        with open(testfile, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(testfile)
        return True
    except Exception:
        return False

def download_worker(repo_id: str, token: str, local_dir: str,
                    ui_queue: "queue.Queue[tuple]",
                    cancel_event: threading.Event):
    try:
        ui_queue.put(("status", "开始下载模型…", "blue"))
        ui_queue.put(("progress_start", None, None))

        os.makedirs(local_dir, exist_ok=True)
        set_env_var(local_dir)

        if cancel_event.is_set():
            ui_queue.put(("status", "已取消（开始前）", "orange"))
            ui_queue.put(("progress_stop", None, None))
            return

        snapshot_download(
            repo_id=repo_id,
            token=token,
            repo_type="model",
            local_dir=local_dir,
            local_dir_use_symlinks=False,
        )

        if cancel_event.is_set():
            ui_queue.put(("status", "已取消（下载结束前收到取消信号）", "orange"))
            ui_queue.put(("progress_stop", None, None))
            return

        ui_queue.put(("progress_stop", None, None))
        ui_queue.put(("status", "模型下载完成！", "green"))
        ui_queue.put(("msgbox", "info", ("成功", "模型已成功下载到指定路径！")))
    except Exception as e:
        ui_queue.put(("progress_stop", None, None))
        ui_queue.put(("status", f"下载失败：{e}", "red"))
        ui_queue.put(("msgbox", "error", ("错误", f"下载出错：{e}")))

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("OpenMind Hub 模型下载工具")
        self.root.geometry("660x430")
        self.root.resizable(False, False)

        self.ui_queue: "queue.Queue[tuple]" = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker_thread: threading.Thread | None = None

        style = ttk.Style(root)
        style.configure("TLabel", font=("Arial", 10))
        style.configure("TEntry", font=("Arial", 10))
        style.configure("TButton", font=("Arial", 10))

        ttk.Label(root, text="模型仓库ID：").place(x=40, y=40, width=110, height=30)
        self.repo_id = ttk.Entry(root)
        self.repo_id.place(x=160, y=40, width=450, height=30)
        self.repo_id.insert(0, "Modelers_Park/DeepSeek-V3.1-w8a8")

        ttk.Label(root, text="魔乐社区令牌：").place(x=40, y=90, width=110, height=30)
        self.token = ttk.Entry(root, show="*")
        self.token.place(x=160, y=90, width=340, height=30)

        self.show_token_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(root, text="显示", variable=self.show_token_var,
                        command=self.toggle_token).place(x=515, y=92, width=70, height=28)

        ttk.Label(root, text="本地存放路径：").place(x=40, y=140, width=110, height=30)
        self.local_dir = ttk.Entry(root)
        self.local_dir.place(x=160, y=140, width=340, height=30)

        default_dir = os.path.join(os.path.expanduser("~"), "Downloads", "openmind_models")
        self.local_dir.insert(0, default_dir)

        ttk.Button(root, text="选择路径", command=self.select_dir)\
            .place(x=515, y=140, width=95, height=30)

        self.btn_start = ttk.Button(root, text="开始下载模型", command=self.start_download)
        self.btn_start.place(x=185, y=200, width=150, height=42)

        self.btn_cancel = ttk.Button(root, text="取消", command=self.cancel_download, state="disabled")
        self.btn_cancel.place(x=355, y=200, width=110, height=42)

        self.progress = ttk.Progressbar(root, mode="indeterminate")
        self.progress.place(x=40, y=275, width=570, height=18)

        self.status = ttk.Label(root, text="就绪", foreground="black")
        self.status.place(x=40, y=305, width=570, height=30)

        self.root.after(100, self.process_ui_queue)

    def toggle_token(self):
        self.token.config(show="" if self.show_token_var.get() else "*")

    def select_dir(self):
        d = filedialog.askdirectory(title="选择模型存放文件夹")
        if d:
            self.local_dir.delete(0, tk.END)
            self.local_dir.insert(0, d)

    def set_busy(self, busy: bool):
        self.btn_start.config(state="disabled" if busy else "normal")
        self.btn_cancel.config(state="normal" if busy else "disabled")

    def start_download(self):
        repo_id = self.repo_id.get().strip()
        token = self.token.get().strip()
        local_dir = self.local_dir.get().strip()

        if not repo_id:
            messagebox.warning("提示", "请填写模型仓库ID！")
            return
        if not token:
            messagebox.warning("提示", "请填写魔乐社区令牌！")
            return
        if not local_dir:
            messagebox.warning("提示", "请选择/填写本地存放路径！")
            return
        if not is_dir_writable(local_dir):
            messagebox.showerror("错误", "该目录不可写，请换一个路径（建议：下载目录/文档目录）。")
            return

        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.warning("提示", "正在下载中，请先等待完成或点击取消。")
            return

        self.cancel_event.clear()
        self.set_busy(True)

        self.worker_thread = threading.Thread(
            target=download_worker,
            args=(repo_id, token, local_dir, self.ui_queue, self.cancel_event),
            daemon=True
        )
        self.worker_thread.start()

    def cancel_download(self):
        self.cancel_event.set()
        self.status.config(text="已发送取消信号，等待当前下载动作结束…", foreground="orange")

    def process_ui_queue(self):
        try:
            while True:
                kind, a, b = self.ui_queue.get_nowait()

                if kind == "status":
                    text, color = a, b
                    self.status.config(text=text, foreground=color)

                elif kind == "progress_start":
                    self.progress.start(10)

                elif kind == "progress_stop":
                    self.progress.stop()
                    self.set_busy(False)

                elif kind == "msgbox":
                    mtype, payload = a, b
                    title, content = payload
                    if mtype == "info":
                        messagebox.showinfo(title, content)
                    else:
                        messagebox.showerror(title, content)
        except queue.Empty:
            pass

        if self.worker_thread and (not self.worker_thread.is_alive()):
            self.set_busy(False)

        self.root.after(100, self.process_ui_queue)

def main():
    root = tk.Tk()
    App(root)
    root.mainloop()

if __name__ == "__main__":
    main()