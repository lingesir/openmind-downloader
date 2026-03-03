import os
import sys
import threading
import queue
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import traceback

# ----------------- 工具函数 -----------------
def set_env_var(local_dir: str):
    """
    设置白名单路径，解决 openmind_hub 可能的目录权限/白名单限制
    """
    os.environ["HUB_WHITE_LIST_PATHS"] = local_dir

def is_dir_writable(path: str) -> bool:
    """
    测试目录是否可写
    """
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
    """
    Windows 友好默认路径：用户下载目录/openmind_models
    """
    home = os.path.expanduser("~")
    return os.path.join(home, "Downloads", "openmind_models")

# ----------------- 下载线程 -----------------
def download_worker(repo_id: str, token: str, local_dir: str,
                    ui_queue: "queue.Queue[tuple]"):
    """
    后台线程：不直接操作 UI，只往 ui_queue 发消息
    """
    start_ts = time.time()

    def log(msg: str):
        ui_queue.put(("log", msg))

    def status(msg: str, color: str = "blue"):
        ui_queue.put(("status", msg, color))

    try:
        status("准备环境/校验目录…", "blue")
        log(f"[INFO] repo_id={repo_id}")
        log(f"[INFO] local_dir={local_dir}")

        os.makedirs(local_dir, exist_ok=True)
        if not is_dir_writable(local_dir):
            raise RuntimeError("目标目录不可写，请更换到下载目录/文档目录或以管理员运行。")

        set_env_var(local_dir)
        status("加载 openmind_hub 模块…", "blue")
        log("[INFO] Importing openmind_hub…（首次可能稍慢）")

        # 延迟导入，避免启动就崩
        from openmind_hub import snapshot_download

        status("连接仓库/获取文件清单（可能需要几十秒）…", "blue")
        log("[INFO] Calling snapshot_download…（如果长时间无响应，多半是网络/代理/证书/鉴权问题）")

        ui_queue.put(("progress_start", None, None))

        # 这里是阻塞下载：网速不一定立刻拉满，会先做鉴权/列目录/校验等准备工作
        snapshot_download(
            repo_id=repo_id,
            token=token,
            repo_type="model",
            local_dir=local_dir,
            local_dir_use_symlinks=False,
        )

        ui_queue.put(("progress_stop", None, None))
        status("下载完成！", "green")

        elapsed = time.time() - start_ts
        log(f"[OK] 下载完成，用时 {elapsed:.1f}s")
        ui_queue.put(("done", True, None))

    except Exception as e:
        ui_queue.put(("progress_stop", None, None))
        status("下载失败（请查看下方日志）", "red")

        log("[ERROR] 发生异常：")
        log(str(e))
        log(traceback.format_exc())

        ui_queue.put(("done", False, str(e)))

# ----------------- GUI 应用 -----------------
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("OpenMind Hub 模型下载工具")
        self.root.geometry("760x520")
        self.root.resizable(False, False)

        self.ui_queue: "queue.Queue[tuple]" = queue.Queue()
        self.worker_thread: threading.Thread | None = None

        style = ttk.Style(root)
        style.configure("TLabel", font=("Microsoft YaHei", 10))
        style.configure("TEntry", font=("Microsoft YaHei", 10))
        style.configure("TButton", font=("Microsoft YaHei", 10))

        # --- Repo ID ---
        ttk.Label(root, text="模型仓库ID：").place(x=40, y=30, width=110, height=30)
        self.repo_id = ttk.Entry(root)
        self.repo_id.place(x=160, y=30, width=560, height=30)
        self.repo_id.insert(0, "Modelers_Park/DeepSeek-V3.1-w8a8")

        # --- Token ---
        ttk.Label(root, text="魔乐社区令牌：").place(x=40, y=80, width=110, height=30)
        self.token = ttk.Entry(root, show="*")
        self.token.place(x=160, y=80, width=470, height=30)

        self.show_token_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(root, text="显示", variable=self.show_token_var, command=self.toggle_token)\
            .place(x=640, y=82, width=70, height=28)

        # --- Local dir ---
        ttk.Label(root, text="本地存放路径：").place(x=40, y=130, width=110, height=30)
        self.local_dir = ttk.Entry(root)
        self.local_dir.place(x=160, y=130, width=470, height=30)
        self.local_dir.insert(0, get_default_download_dir())

        ttk.Button(root, text="选择路径", command=self.select_dir)\
            .place(x=640, y=130, width=80, height=30)

        # --- Buttons ---
        self.btn_start = ttk.Button(root, text="开始下载模型", command=self.start_download)
        self.btn_start.place(x=220, y=180, width=140, height=42)

        self.btn_cancel = ttk.Button(root, text="取消", command=self.cancel_download, state="disabled")
        self.btn_cancel.place(x=400, y=180, width=100, height=42)

        # --- Progress ---
        self.progress = ttk.Progressbar(root, mode="indeterminate")
        self.progress.place(x=40, y=240, width=680, height=16)

        # --- Status ---
        self.status_label = ttk.Label(root, text="就绪", foreground="black")
        self.status_label.place(x=40, y=265, width=680, height=26)

        # --- Log box ---
        ttk.Label(root, text="日志：").place(x=40, y=295, width=50, height=24)
        self.log_text = tk.Text(root, wrap="word", font=("Consolas", 10))
        self.log_text.place(x=40, y=320, width=680, height=170)
        self.log_text.insert("end", "提示：如果长时间停留在“连接仓库/获取文件清单…”，通常是网络/代理/证书/鉴权问题。\n")
        self.log_text.config(state="disabled")

        # 定时处理队列
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

    def append_log(self, msg: str):
        self.log_text.config(state="normal")
        self.log_text.insert("end", msg.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

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

        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.warning("提示", "正在下载中，请先等待完成或点击取消。")
            return

        # UI 切换忙碌状态
        self.set_busy(True)
        self.status_label.config(text="准备开始…", foreground="blue")
        self.append_log("------------------------------------------------------------")
        self.append_log("[UI] 开始下载任务…")

        # 启动线程
        self.worker_thread = threading.Thread(
            target=download_worker,
            args=(repo_id, token, local_dir, self.ui_queue),
            daemon=True
        )
        self.worker_thread.start()

    def cancel_download(self):
        # snapshot_download 是阻塞下载，外部无法安全中断。
        # 最可靠的取消方式：直接退出进程终止下载。
        ok = messagebox.askyesno("确认取消", "取消会立即终止程序以停止下载（不会损坏系统）。确定吗？")
        if ok:
            os._exit(0)

    def process_ui_queue(self):
        try:
            while True:
                kind, a, b = self.ui_queue.get_nowait()

                if kind == "status":
                    text, color = a, b
                    self.status_label.config(text=text, foreground=color)

                elif kind == "log":
                    self.append_log(a)

                elif kind == "progress_start":
                    self.progress.start(10)

                elif kind == "progress_stop":
                    self.progress.stop()

                elif kind == "done":
                    success, err = a, b
                    self.set_busy(False)
                    if success:
                        messagebox.showinfo("成功", "模型下载完成！")
                    else:
                        messagebox.showerror("失败", f"下载失败：{err}\n\n请查看日志窗口定位原因。")

        except queue.Empty:
            pass

        # 如果线程已结束，但没收到 done（极少数情况），也恢复按钮
        if self.worker_thread and (not self.worker_thread.is_alive()):
            self.set_busy(False)

        self.root.after(100, self.process_ui_queue)

def main():
    root = tk.Tk()
    App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
