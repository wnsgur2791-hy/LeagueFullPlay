"""리그 풀플레이 - 컴투스 프로야구 V26 리그모드 직접플레이 자동 진행 프로그램"""
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

APP_DIR = os.path.dirname(os.path.abspath(__file__))   # 프로그램 코드 (업데이트로 바뀜)
ROOT_DIR = os.path.dirname(APP_DIR)                     # 내 설정·로그·저장 화면 (업데이트로 안 바뀜)
os.chdir(ROOT_DIR)
sys.path.insert(0, APP_DIR)

import updater  # noqa: E402
from adb_client import Adb, AdbError  # noqa: E402
from bot import Bot, Brain, save_capture  # noqa: E402
from version import VERSION  # noqa: E402
from vision import Vision  # noqa: E402

DEFAULT_CONFIG = os.path.join(APP_DIR, "config_default.json")
USER_CONFIG = os.path.join(ROOT_DIR, "config.json")


def _defaults():
    with open(DEFAULT_CONFIG, encoding="utf-8") as f:
        return json.load(f)


def load_config():
    """기본 설정 위에 내 설정(config.json)을 덮어씀. 새 버전에서 생긴 설정은 기본값으로 채워짐"""
    cfg = _defaults()
    try:
        with open(USER_CONFIG, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"config.json을 읽지 못해서 기본 설정을 씁니다: {e}")
    return cfg


def save_config(cfg):
    """기본값과 다른 설정만 config.json에 저장 (그래야 새 버전의 기본값 개선이 그대로 적용됨)"""
    base = _defaults()
    mine = {k: v for k, v in cfg.items() if base.get(k) != v}
    with open(USER_CONFIG, "w", encoding="utf-8") as f:
        json.dump(mine, f, ensure_ascii=False, indent=2)


class App:
    def __init__(self, root):
        self.root = root
        root.title(f"리그 풀플레이 v{VERSION}")
        self.cfg = load_config()
        self.vision = Vision(os.path.join(APP_DIR, "templates"))
        self.adb = None
        self.bot = None
        self.q = queue.Queue()
        self.started_at = None
        os.makedirs("logs", exist_ok=True)
        self.log_path = os.path.join("logs", f"log_{datetime.now():%Y%m%d}.txt")

        pad = {"padx": 8, "pady": 4}
        top = ttk.Frame(root)
        top.pack(fill="x", **pad)
        self.conn_var = tk.StringVar(value="연결 안 됨")
        ttk.Label(top, text="LDPlayer:").pack(side="left")
        ttk.Label(top, textvariable=self.conn_var).pack(side="left", padx=6)

        btns = ttk.Frame(root)
        btns.pack(fill="x", **pad)
        self.btn_connect = ttk.Button(btns, text="연결 확인", command=self.connect)
        self.btn_start = ttk.Button(btns, text="▶ 시작", command=self.start)
        self.btn_stop = ttk.Button(btns, text="■ 정지", command=self.stop, state="disabled")
        self.btn_save = ttk.Button(btns, text="화면 저장", command=self.save_screen)
        self.btn_test = ttk.Button(btns, text="인식 테스트", command=self.test_screen)
        for b in (self.btn_connect, self.btn_start, self.btn_stop, self.btn_save, self.btn_test):
            b.pack(side="left", padx=3)

        modef = ttk.Frame(root)
        modef.pack(fill="x", **pad)
        ttk.Label(modef, text="플레이 방식:").pack(side="left")
        self.mode_var = tk.StringVar(value=self.cfg.get("mode", "full"))
        ttk.Radiobutton(modef, text="풀플레이", value="full", variable=self.mode_var,
                        command=self.change_mode).pack(side="left", padx=6)
        ttk.Radiobutton(modef, text="하이라이트", value="highlight", variable=self.mode_var,
                        command=self.change_mode).pack(side="left", padx=6)

        opt = ttk.Frame(root)
        opt.pack(fill="x", **pad)
        ttk.Label(opt, text="최대 경기 수 (0 = 무제한):").pack(side="left")
        self.max_var = tk.StringVar(value=str(self.cfg.get("max_games", 0)))
        ttk.Spinbox(opt, from_=0, to=144, width=6, textvariable=self.max_var).pack(side="left", padx=6)
        self.auto_upd_var = tk.BooleanVar(value=self.cfg.get("auto_update", True))
        ttk.Checkbutton(opt, text="켤 때 자동 업데이트", variable=self.auto_upd_var,
                        command=self.toggle_auto_update).pack(side="left", padx=10)
        self.btn_update = ttk.Button(opt, text="업데이트 확인", command=self.check_update)
        self.btn_update.pack(side="left")

        stat = ttk.LabelFrame(root, text="진행 상황")
        stat.pack(fill="x", **pad)
        self.screen_var = tk.StringVar(value="-")
        self.games_var = tk.StringVar(value="0")
        self.sim_var = tk.StringVar(value="0")
        self.time_var = tk.StringVar(value="00:00:00")
        rows = [("현재 화면", self.screen_var), ("완료한 경기", self.games_var),
                ("시뮬레이션 전환", self.sim_var), ("실행 시간", self.time_var)]
        for i, (k, v) in enumerate(rows):
            ttk.Label(stat, text=k).grid(row=i, column=0, sticky="w", padx=8, pady=2)
            ttk.Label(stat, textvariable=v).grid(row=i, column=1, sticky="w", padx=8, pady=2)

        logf = ttk.LabelFrame(root, text="로그")
        logf.pack(fill="both", expand=True, **pad)
        self.log_box = tk.Text(logf, height=18, width=72, state="disabled", wrap="word")
        sb = ttk.Scrollbar(logf, command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=sb.set)
        self.log_box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.poll)
        self.log(f"리그 풀플레이 v{VERSION}을 켰습니다. 먼저 [연결 확인]을 눌러주세요.")
        self.root.after(3000, self.confirm_update)

    # ---------- 업데이트 ----------
    def confirm_update(self):
        """새 버전이 정상으로 켜졌다고 표시 (안 하면 다음에 켤 때 이전 버전으로 되돌림)"""
        try:
            m = updater.confirm_ok()
        except Exception:
            m = None
        if m:
            self.log(f"v{m.get('from')} → v{m.get('to')} 으로 업데이트됐어요.")
            for line in str(m.get("notes") or "").splitlines():
                self.log(f"  {line}")

    def change_mode(self):
        self.cfg["mode"] = self.mode_var.get()
        save_config(self.cfg)
        name = "하이라이트" if self.cfg["mode"] == "highlight" else "풀플레이"
        self.log(f"플레이 방식: {name} (리그 메인/플레이 방식 선택 화면에서 이 방식으로 들어가요)")

    def toggle_auto_update(self):
        self.cfg["auto_update"] = bool(self.auto_upd_var.get())
        save_config(self.cfg)
        self.log("켤 때 자동 업데이트: " + ("켬" if self.cfg["auto_update"] else "끔"))

    def check_update(self):
        if self.bot and self.bot.is_alive():
            messagebox.showinfo("업데이트", "자동 진행을 멈춘 뒤에 확인해주세요.")
            return
        self.btn_update.configure(state="disabled")
        self.log("새 버전 확인 중...")

        def work():
            try:
                v = updater.update_now(lambda m: self.q.put(("log", m.strip())))
                self.q.put(("update_done", v))
            except Exception as e:
                self.q.put(("log", f"업데이트 확인 실패: {e}"))
                self.q.put(("update_done", False))
        threading.Thread(target=work, daemon=True).start()

    def on_update_done(self, v):
        self.btn_update.configure(state="normal")
        if v:
            self.log(f"v{v}을 받았어요. 프로그램을 껐다가 run.bat으로 다시 켜면 적용돼요.")
            messagebox.showinfo("업데이트", f"v{v}을 받았어요.\n프로그램을 껐다가 run.bat으로 다시 켜면 적용돼요.")
        elif v is None:
            self.log("지금이 최신 버전이에요.")

    # ---------- 공통 ----------
    def log(self, msg):
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def poll(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self.log(data)
                elif kind == "status":
                    screen, games, sims = data
                    self.screen_var.set(screen)
                    self.games_var.set(str(games))
                    self.sim_var.set(str(sims))
                elif kind == "stop":
                    self.on_bot_stopped(data)
                elif kind == "update_done":
                    self.on_update_done(data)
        except queue.Empty:
            pass
        if self.started_at:
            s = int(time.time() - self.started_at)
            self.time_var.set(f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}")
        self.root.after(200, self.poll)

    def ensure_adb(self):
        if self.adb is None:
            self.connect()
        return self.adb is not None

    # ---------- 버튼 ----------
    def connect(self):
        try:
            adb = Adb(self.cfg.get("adb_path", ""), self.cfg.get("serial", ""))
            serial, devices = adb.connect()
            frame = adb.screencap()
        except AdbError as e:
            self.adb = None
            self.conn_var.set("연결 실패")
            self.log(f"연결 실패: {e}")
            if "adb.exe를 찾지" in str(e):
                if messagebox.askyesno("adb.exe 찾기",
                                       "LDPlayer의 adb.exe를 찾지 못했어요.\n"
                                       "LDPlayer 설치 폴더에서 adb.exe를 직접 고를까요?\n"
                                       "(보통 C:\\LDPlayer\\LDPlayer9 폴더 안에 있어요)"):
                    path = filedialog.askopenfilename(title="adb.exe 고르기",
                                                      filetypes=[("adb", "adb.exe"), ("모든 파일", "*.*")])
                    if path:
                        self.cfg["adb_path"] = path
                        save_config(self.cfg)
                        self.log(f"adb 위치를 저장했어요: {path}")
                        self.connect()
                return
            messagebox.showerror("연결 실패", str(e))
            return
        self.adb = adb
        w, h = adb.size
        self.conn_var.set(f"{serial}  ({w}x{h})")
        self.log(f"연결됨: {serial}, 화면 {w}x{h}, adb: {adb.adb}")
        if len(devices) > 1:
            self.log(f"기기가 여러 개입니다 {devices}. 다른 기기를 쓰려면 config.json의 serial에 적어주세요.")
        if w > h:
            self.log("⚠ 화면이 가로로 잡힙니다. 게임은 세로 화면이라 인식이 안 될 수 있어요.")
        elif abs(h / w - 16 / 9) > 0.03:
            self.log("⚠ 화면 비율이 9:16이 아닙니다. LDPlayer 해상도를 900x1600 같은 9:16으로 맞추면 정확해요.")

    def start(self):
        if self.bot and self.bot.is_alive():
            return
        if not self.ensure_adb():
            return
        try:
            self.cfg["max_games"] = max(0, int(self.max_var.get()))
        except ValueError:
            self.cfg["max_games"] = 0
        self.cfg["mode"] = self.mode_var.get()
        save_config(self.cfg)
        self.log("플레이 방식: " + ("하이라이트" if self.cfg["mode"] == "highlight" else "풀플레이"))
        self.bot = Bot(self.adb, self.vision, self.cfg,
                       on_log=lambda m: self.q.put(("log", m)),
                       on_status=lambda *a: self.q.put(("status", a)),
                       on_stop=lambda r: self.q.put(("stop", r)))
        self.started_at = time.time()
        self.bot.start()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.btn_connect.configure(state="disabled")

    def stop(self):
        if self.bot:
            self.bot.stop()
            self.log("정지하는 중...")

    def on_bot_stopped(self, reason):
        self.log(f"멈춤: {reason}")
        self.started_at = None
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.btn_connect.configure(state="normal")
        if "정지 버튼" not in reason:
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass
            messagebox.showinfo("리그 풀플레이 멈춤", reason)

    def grab(self):
        if self.bot and self.bot.is_alive() and self.bot.last_frame is not None:
            return self.bot.last_frame
        if not self.ensure_adb():
            return None
        try:
            return self.adb.screencap()
        except AdbError as e:
            self.log(f"캡처 실패: {e}")
            return None

    def save_screen(self):
        frame = self.grab()
        if frame is not None:
            path = save_capture(frame, "화면")
            self.log(f"화면을 저장했습니다: {os.path.join(ROOT_DIR, path)}")

    def test_screen(self):
        """지금 화면을 인식해서 점수를 보여줌 (아무것도 누르지 않음)"""
        frame = self.grab()
        if frame is None:
            return
        norm, _, _ = Vision.normalize(frame)
        info = self.vision.analyze(norm)
        brain = Brain(self.cfg)
        screen, taps, _logs, _n, _s = brain.step(info, time.time())
        self.log(f"── 인식 테스트: '{screen}' 으로 판단 (누를 위치 {taps or '없음'}) ──")
        parts = []
        for name in self.vision.templates:
            ok, score, _pt = info[name]
            parts.append(f"{'●' if ok else '○'}{name} {score:.2f}")
        self.log(" / ".join(parts))
        self.log(f"게이지 135 노란 픽셀: {info['_yellow135']} (기준 {self.cfg['gauge_yellow_min_px']} 이상이면 달성)")
        path = save_capture(frame, "테스트")
        self.log(f"테스트 화면 저장: {path}")

    def on_close(self):
        if self.bot and self.bot.is_alive():
            if not messagebox.askyesno("종료", "자동 진행 중입니다. 멈추고 종료할까요?"):
                return
            self.bot.stop()
        self.root.destroy()


def main():
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
