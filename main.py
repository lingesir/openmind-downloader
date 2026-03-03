import os
import threading
import queue
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import traceback

def now_ts():
    return time.strftime("%H:%M:%S")

def norm_abs(p: str) -> str:
    # 统一成绝对路径 + 规范化分隔符，避免白名单匹配失败
    return os.path.normpath(os.path.abspath(p))

def append_whitelist_path(path: str):
    """
    把 path 加入 HUB_WHITE_LIST_PATHS（用 ; 分隔，兼容 Windows）
    关键：必须在 import openmind_hub 之前设置才最稳
    """
    path = norm_abs(path)
    old = os.environ.get("HUB_WHITE_LIST_PATHS", "").strip()
    if not old:
        os.environ["HUB_WHITE_LIST_PATHS"] = path
        return
    parts = [x.strip() for x in old.split(";") if x.strip()]
    if path not in parts:
        parts.append(path)
    os.environ["HUB_WHITE_LIST_PATHS"] = ";".join(parts)

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

def get_default_download_dir() -> str:
    home = os.path.expanduser("~")
    return os.path.join(home, "Downloads", "openmind_models")

# ----------------- 下载线程 -----------------
def download_worker(repo_id: str, token: str, local_dir: str, uiq: "queue.Queue[tuple]"):
    start = time.time()

    def log(msg: str):
        uiq.put(("log", f"[{now_ts()}] {msg}"))

    def status(msg: str, color: str = "blue"):
        uiq.put(("status", msg, color))

    try:
        local_dir = norm_abs(local_dir)

        status("准备环境/校验目录…", "blue")
        log(f"任务开始 repo_id={repo_id}")
        log(f"目标目录 local_dir={local_dir}")

        os.makedirs(local_dir, exist_ok=True)
        if not is_dir_writable(local_dir):
            raise RuntimeError("目标目录不可写，请更换目录或以管理员运行。")

        # ✅ 最关键：再次确保白名单包含 local_dir（保险起见）
        append_whitelist_path(local_dir)
        log(f"HUB_WHITE_LIST_PATHS={os.environ.get('HUB_WHITE_LIST_PATHS','')}")

        # ✅ 必须在 import 前设置好白名单（这里已经设置了）
        status("加载 openmind_hub（首次可能较慢）…", "blue")
        log("即将 import openmind_hub …")
        t0 = time.time()
        from openmind_hub import snapshot_download
        t1 = time.time()
        log(f"import openmind_hub 完成，用时 {t1 - t0:.2f}s")

        status("连接仓库/获取文件清单…", "blue")
        log("调用 snapshot_download（此阶段可能访问 API/CDN 域名，不一定是 modelers.cn 首页）")
        uiq.put(("progress_start", None, None))

        t2 = time.time()
        snapshot_download(
            repo_id=repo_id,
            token=token,
            repo_type="model",
            local_dir=local_dir,
            local_dir_use_symlinks=False,  # ✅ 强制实体文件
        )
        t3 = time.time()
        log(f"snapshot_download 返回，用时 {t3 - t2:.2f}s")

        uiq.put(("progress_stop", None, None))
        status("下载完成！", "green")
        log(f"总耗时 {time.time() - start:.1f}s")
        uiq.put(("done", True, None))

    except Exception as e:
        uiq.put(("progress_stop", None, None))
        status("下载失败（请看日志）", "red")
        log("发生异常：")
        log(str(e))
        log(traceback.format_exc())
        uiq.put(("done", False, str(e)))

# ----------------- GUI -----------------
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("OpenMind Hub 模型下载工具")
        self.root.geometry("780x560")
        self.root.resizable(False, False)

        self.uiq: "queue.Queue[tuple]" = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.heartbeat_on = False

        style = ttk.Style(root)
        style.configure("TLabel", font=("Microsoft YaHei", 10))
        style.configure("TEntry", font=("Microsoft YaHei", 10))
        style.configure("TButton", font=("Microsoft YaHei", 10))

        ttk.Label(root, text="模型仓库ID：").place(x=40, y=30, width=110, height=30)
        self.repo_id = ttk.Entry(root)
        self.repo_id.place(x=160, y=30, width=580, height=30)
        self.repo_id.insert(0, "Qwen-AI/Qwen3-VL-Embedding-8B")

        ttk.Label(root, text="魔乐社区令牌：").place(x=40, y=80, width=110, height=30)
        self.token = ttk.Entry(root, show="*")
        self.token.place(x=160, y=80, width=490, height=30)

        self.show_token = tk.BooleanVar(value=False)
        ttk.Checkbutton(root, text="显示", variable=self.show_token, command=self.toggle_token)\
            .place(x=660, y=82, width=70, height=28)

        ttk.Label(root, text="本地存放路径：").place(x=40, y=130, width=110, height=30)
        self.local_dir = ttk.Entry(root)
        self.local_dir.place(x=160, y=130, width=490, height=30)
        self.local_dir.insert(0, get_default_download_dir())

        ttk.Button(root, text="选择路径", command=self.select_dir)\
            .place(x=660, y=130, width=80, height=30)

        self.btn_start = ttk.Button(root, text="开始下载模型", command=self.start_download)
        self.btn_start.place(x=240, y=180, width=150, height=42)

        self.btn_cancel = ttk.Button(root, text="取消", command=self.cancel_download, state="disabled")
        self.btn_cancel.place(x=410, y=180, width=110, height=42)

        self.progress = ttk.Progressbar(root, mode="indeterminate")
        self.progress.place(x=40, y=240, width=700, height=16)

        self.status_label = ttk.Label(root, text="就绪", foreground="black")
        self.status_label.place(x=40, y=265, width=700, height=26)

        ttk.Label(root, text="日志：").place(x=40, y=295, width=50, height=24)
        self.log_text = tk.Text(root, wrap="word", font=("Consolas", 10))
        self.log_text.place(x=40, y=320, width=700, height=210)
        self.log_text.insert("end", "提示：本工具会自动把下载目录加入 HUB_WHITE_LIST_PATHS 白名单，避免权限/卡住问题。\n")
        self.log_text.config(state="disabled")

        # ✅ 程序启动时先把默认下载目录/Temp 加入白名单（非常关键：早于 import openmind_hub）
        try:
            default_dir = norm_abs(self.local_dir.get().strip())
            append_whitelist_path(default_dir)
            temp_dir = norm_abs(os.environ.get("TEMP", default_dir))
            append_whitelist_path(temp_dir)
            # 你也可以把 Downloads 根目录加入，减少误伤
            downloads_root = norm_abs(os.path.join(os.path.expanduser("~"), "Downloads"))
            append_whitelist_path(downloads_root)
            self.append_log(f"[{now_ts()}] 启动白名单 HUB_WHITE_LIST_PATHS={os.environ.get('HUB_WHITE_LIST_PATHS','')}")
        except Exception:
            pass

        self.root.after(100, self.process_ui_queue)

    def toggle_token(self):
        self.token.config(show="" if self.show_token.get() else "*")

    def select_dir(self):
        d = filedialog.askdirectory(title="选择模型存放文件夹")
        if d:
            self.local_dir.delete(0, tk.END)
            self.local_dir.insert(0, d)
            # ✅ 用户改目录后，立刻加入白名单（并且早于后续 import）
            append_whitelist_path(d)
            self.append_log(f"[{now_ts()}] 已加入白名单：{norm_abs(d)}")

    def set_busy(self, busy: bool):
        self.btn_start.config(state="disabled" if busy else "normal")
        self.btn_cancel.config(state="normal" if busy else "disabled")

    def append_log(self, msg: str):
        self.log_text.config(state="normal")
        self.log_text.insert("end", msg.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def start_heartbeat(self):
        self.heartbeat_on = True
        def beat():
            if not self.heartbeat_on:
                return
            if self.worker_thread and self.worker_thread.is_alive():
                self.append_log(f"[{now_ts()}] (heartbeat) 任务仍在运行中…")
                self.root.after(1000, beat)
        self.root.after(1000, beat)

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
            messagebox.warning("提示", "请填写本地存放路径！")
            return
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.warning("提示", "正在下载中，请先等待完成或点击取消。")
            return

        # ✅ 开始前先把用户选的目录加入白名单（早设置）
        append_whitelist_path(local_dir)
        self.append_log(f"[{now_ts()}] 开始前白名单 HUB_WHITE_LIST_PATHS={os.environ.get('HUB_WHITE_LIST_PATHS','')}")

        self.set_busy(True)
        self.status_label.config(text="准备开始…", foreground="blue")
        self.append_log("------------------------------------------------------------")
        self.append_log(f"[{now_ts()}] [UI] 开始下载任务…")

        self.worker_thread = threading.Thread(
            target=download_worker,
            args=(repo_id, token, local_dir, self.uiq),
            daemon=True
        )
        self.worker_thread.start()
        self.start_heartbeat()

    def cancel_download(self):
        ok = messagebox.askyesno("确认取消", "取消会立即退出程序以停止下载（最可靠）。确定吗？")
        if ok:
            os._exit(0)

    def process_ui_queue(self):
        try:
            while True:
                kind, a, b = self.uiq.get_nowait()

                if kind == "status":
                    self.status_label.config(text=a, foreground=b)

                elif kind == "log":
                    self.append_log(a)

                elif kind == "progress_start":
                    self.progress.start(10)

                elif kind == "progress_stop":
                    self.progress.stop()

                elif kind == "done":
                    success, err = a, b
                    self.heartbeat_on = False
                    self.set_busy(False)
                    if success:
                        messagebox.showinfo("成功", "模型下载完成！")
                    else:
                        messagebox.showerror("失败", f"下载失败：{err}\n\n请查看日志定位原因。")
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
