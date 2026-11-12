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

import cv2

APP_DIR = os.path.dirname(os.path.abspath(__file__))   # 프로그램 코드 (업데이트로 바뀜)
ROOT_DIR = os.path.dirname(APP_DIR)                     # 내 설정·로그·저장 화면 (업데이트로 안 바뀜)
os.chdir(ROOT_DIR)
sys.path.insert(0, APP_DIR)

import report  # noqa: E402
import updater  # noqa: E402
from adb_client import Adb, AdbError  # noqa: E402
from bot import WEEK, Bot, make_brain, rc_kind_for, save_capture  # noqa: E402
from version import REPO, VERSION  # noqa: E402
from vision import Vision  # noqa: E402

DEFAULT_CONFIG = os.path.join(APP_DIR, "config_default.json")

# 고급 설정에 보여줄 이름 (화면 흐름 순서)
TEMPLATE_NAMES = {
    "main_title": "리그 메인 (NEXT MATCH)",
    "main_playball": "리그 메인 PLAY BALL 버튼",
    "select_title": "플레이 방식 선택 (SELECT TYPE)",
    "select_start": "START 버튼",
    "catcher_lead": "포수 리드 버튼 (투구 화면)",
    "pitch_off": "투구 OFF 표시",
    "pitch_on": "투구 ON 표시",
    "outs": "경기 중 OUTS 글자",
    "gauge_zero": "미션 게이지 (0칸 체크)",
    "pause_btn": "풀플레이 일시정지 버튼",
    "coach_title": "코치의 조언 제목",
    "coach_replace": "코치의 조언 교체 버튼",
    "hl_view": "하이라이트 화면 (ON THE MOUND)",
    "hl_pause": "하이라이트 일시정지 버튼",
    "hl_title": "하이라이트 직접 플레이 제목",
    "hl_skip": "건너 뛰기 버튼",
    "menu_sim": "메뉴 '시뮬레이션 전환'",
    "menu_continue": "메뉴 '계속 하기'",
    "alert_sim": "알림창 문구",
    "alert_ok": "알림창 확인 버튼",
    "result_title": "경기 결과 제목",
    "result_next": "경기 결과 다음 버튼",
    "mvp_title": "반복과제 보상 제목",
    "mvp_next": "반복과제 보상 다음 버튼",
    "round_again": "한 번 더 하기 버튼",
    "rc_lobby": "랭챌 로비",
    "rc_cont_popup": "랭챌 연속 경기 창",
    "rc_refresh_popup": "랭챌 즉시 갱신 창",
    "rc_series_result": "랭챌 연속 경기 결과",
}
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
    if cfg.get("mode") in ("rc_point", "rc_star"):     # v1.4.1까지의 포갱/스갱 선택 → 요일별 갱신으로
        cfg["mode"] = "rc_custom"
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

        self.vision.set_thresholds(self.cfg.get("thresholds"))
        pad = {"padx": 8, "pady": 4}
        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True)
        main = ttk.Frame(nb)
        adv = ttk.Frame(nb)
        nb.add(main, text="실행")
        nb.add(adv, text="고급 설정")
        self.build_advanced(adv)
        top = ttk.Frame(main)
        top.pack(fill="x", **pad)
        self.conn_var = tk.StringVar(value="연결 안 됨")
        ttk.Label(top, text="LDPlayer:").pack(side="left")
        ttk.Label(top, textvariable=self.conn_var).pack(side="left", padx=6)

        btns = ttk.Frame(main)
        btns.pack(fill="x", **pad)
        self.btn_connect = ttk.Button(btns, text="연결 확인", command=self.connect)
        self.btn_start = ttk.Button(btns, text="▶ 시작", command=self.start)
        self.btn_stop = ttk.Button(btns, text="■ 정지", command=self.stop, state="disabled")
        self.btn_save = ttk.Button(btns, text="화면 저장", command=self.save_screen)
        self.btn_test = ttk.Button(btns, text="인식 테스트", command=self.test_screen)
        self.btn_report = ttk.Button(btns, text="신고·건의", command=self.open_report)
        for b in (self.btn_connect, self.btn_start, self.btn_stop, self.btn_save, self.btn_test, self.btn_report):
            b.pack(side="left", padx=3)

        modef = ttk.LabelFrame(main, text="무엇을 돌릴까요?")
        modef.pack(fill="x", **pad)
        self.mode_var = tk.StringVar(value=self.cfg.get("mode", "full"))
        r1 = ttk.Frame(modef)
        r1.pack(fill="x", padx=6, pady=2)
        ttk.Label(r1, text="리그 모드", width=10).pack(side="left")
        for text, val in (("풀플레이", "full"), ("하이라이트", "highlight")):
            ttk.Radiobutton(r1, text=text, value=val, variable=self.mode_var,
                            command=self.change_mode).pack(side="left", padx=6)
        r2 = ttk.Frame(modef)
        r2.pack(fill="x", padx=6, pady=2)
        ttk.Label(r2, text="랭킹챌린지", width=10).pack(side="left")
        ttk.Radiobutton(r2, text="요일별 갱신 (아래 표대로 포갱/스갱)", value="rc_custom", variable=self.mode_var,
                        command=self.change_mode).pack(side="left", padx=6)
        r3 = ttk.Frame(modef)
        r3.pack(fill="x", padx=6, pady=(2, 6))
        ttk.Label(r3, text="요일별 갱신", width=10).pack(side="left")
        week = list(self.cfg.get("rc_week") or ["point"] * 7)
        self.week_vars = []
        for i, day in enumerate(WEEK):
            ttk.Label(r3, text=day).pack(side="left", padx=(4, 1))
            v = tk.StringVar(value="스갱" if week[i] == "star" else "포갱")
            cb = ttk.Combobox(r3, textvariable=v, values=("포갱", "스갱"), width=4, state="readonly")
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", lambda e: self.change_week())
            self.week_vars.append(v)
        self.today_var = tk.StringVar()
        ttk.Label(r3, textvariable=self.today_var, foreground="#1a6fb0").pack(side="left", padx=8)
        self.update_today_label()

        opt = ttk.Frame(main)
        opt.pack(fill="x", **pad)
        ttk.Label(opt, text="최대 경기 수 (랭챌은 연속 경기 수, 0 = 무제한):").pack(side="left")
        self.max_var = tk.StringVar(value=str(self.cfg.get("max_games", 0)))
        ttk.Spinbox(opt, from_=0, to=144, width=6, textvariable=self.max_var).pack(side="left", padx=6)
        self.auto_upd_var = tk.BooleanVar(value=self.cfg.get("auto_update", True))
        ttk.Checkbutton(opt, text="켤 때 자동 업데이트", variable=self.auto_upd_var,
                        command=self.toggle_auto_update).pack(side="left", padx=10)
        self.btn_update = ttk.Button(opt, text="업데이트 확인", command=self.check_update)
        self.btn_update.pack(side="left")

        stat = ttk.LabelFrame(main, text="진행 상황")
        stat.pack(fill="x", **pad)
        self.screen_var = tk.StringVar(value="-")
        self.games_var = tk.StringVar(value="0")
        self.sim_var = tk.StringVar(value="0")
        self.time_var = tk.StringVar(value="00:00:00")
        self.games_label = tk.StringVar(value="완료한 경기")
        self.sim_label = tk.StringVar(value="시뮬레이션 전환")
        rows = [(tk.StringVar(value="현재 화면"), self.screen_var), (self.games_label, self.games_var),
                (self.sim_label, self.sim_var), (tk.StringVar(value="실행 시간"), self.time_var)]
        for i, (k, v) in enumerate(rows):
            ttk.Label(stat, textvariable=k).grid(row=i, column=0, sticky="w", padx=8, pady=2)
            ttk.Label(stat, textvariable=v).grid(row=i, column=1, sticky="w", padx=8, pady=2)

        logf = ttk.LabelFrame(main, text="로그")
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

    # ---------- 신고·건의 ----------
    def open_report(self):
        if getattr(self, "report_win", None) and self.report_win.winfo_exists():
            self.report_win.lift()
            return
        win = tk.Toplevel(self.root)
        win.title("신고·건의")
        self.report_win = win
        pad = {"padx": 10, "pady": 4}

        kindf = ttk.Frame(win)
        kindf.pack(fill="x", **pad)
        ttk.Label(kindf, text="종류:").pack(side="left")
        kind_var = tk.StringVar(value="bug")
        ttk.Radiobutton(kindf, text="고장 신고", value="bug", variable=kind_var).pack(side="left", padx=6)
        ttk.Radiobutton(kindf, text="건의", value="idea", variable=kind_var).pack(side="left", padx=6)

        namef = ttk.Frame(win)
        namef.pack(fill="x", **pad)
        ttk.Label(namef, text="이름(선택):").pack(side="left")
        name_var = tk.StringVar(value=self.cfg.get("report_name", ""))
        ttk.Entry(namef, textvariable=name_var, width=24).pack(side="left", padx=6)

        ttk.Label(win, text="내용 (어떤 화면에서 무엇이 잘못됐는지, 또는 바라는 점을 적어주세요)").pack(anchor="w", **pad)
        text = tk.Text(win, width=60, height=8, wrap="word")
        text.pack(fill="both", expand=True, padx=10)

        attf = ttk.LabelFrame(win, text="고장 신고에 같이 보낼 것")
        attf.pack(fill="x", **pad)
        log_var = tk.BooleanVar(value=True)
        scr_var = tk.BooleanVar(value=True)
        cb_log = ttk.Checkbutton(attf, text="최근 로그", variable=log_var)
        cb_scr = ttk.Checkbutton(attf, text="지금 게임 화면 사진 (게임 닉네임이 보일 수 있어요)", variable=scr_var)
        cb_log.pack(anchor="w", padx=6)
        cb_scr.pack(anchor="w", padx=6)
        ttk.Label(attf, text="버전·플레이 방식·해상도·현재 화면 판단은 자동으로 같이 가요.",
                  foreground="#666666").pack(anchor="w", padx=6, pady=(0, 4))

        def on_kind(*_):
            st = "normal" if kind_var.get() == "bug" else "disabled"
            cb_log.configure(state=st)
            cb_scr.configure(state=st)
            attf.configure(text="고장 신고에 같이 보낼 것" if st == "normal" else "건의는 글만 보내요")
        kind_var.trace_add("write", on_kind)

        btnf = ttk.Frame(win)
        btnf.pack(fill="x", **pad)
        send_btn = ttk.Button(btnf, text="보내기")
        send_btn.pack(side="right")
        ttk.Button(btnf, text="닫기", command=win.destroy).pack(side="right", padx=6)

        def do_send():
            body = text.get("1.0", "end").strip()
            if not body:
                messagebox.showinfo("신고·건의", "내용을 적어주세요.", parent=win)
                return
            wait = 30 - (time.time() - getattr(self, "last_report", 0))
            if wait > 0:
                messagebox.showinfo("신고·건의", f"{int(wait) + 1}초 뒤에 다시 보낼 수 있어요.", parent=win)
                return
            self.cfg["report_name"] = name_var.get().strip()
            save_config(self.cfg)
            send_btn.configure(state="disabled", text="보내는 중...")
            kind = kind_var.get()
            opts = {"log": kind == "bug" and log_var.get(), "screen": kind == "bug" and scr_var.get()}
            threading.Thread(target=self._send_report, daemon=True,
                             args=(kind, name_var.get(), body, opts, win, send_btn)).start()
        send_btn.configure(command=do_send)

    def _send_report(self, kind, name, body, opts, win, send_btn):
        try:
            info = []
            log_text = screen = None
            if kind == "bug":
                w_h = f"{self.adb.size[0]}x{self.adb.size[1]}" if self.adb and self.adb.size else "연결 안 됨"
                mode = "하이라이트" if self.cfg.get("mode") == "highlight" else "풀플레이"
                info = [f"v{VERSION} · {mode} · {w_h} · 현재 화면: {self.screen_var.get()} · "
                        f"완료 {self.games_var.get()}경기"]
                if opts["log"]:
                    try:
                        with open(self.log_path, encoding="utf-8") as f:
                            log_text = "".join(f.readlines()[-200:])
                    except OSError:
                        log_text = None
                if opts["screen"]:
                    frame = None
                    if self.bot and self.bot.is_alive() and self.bot.last_frame is not None:
                        frame = self.bot.last_frame
                    elif self.adb:
                        try:
                            frame = self.adb.screencap()
                        except Exception:
                            frame = None
                    if frame is not None:
                        h, w = frame.shape[:2]
                        small = cv2.resize(frame, (540, int(h * 540 / w)), interpolation=cv2.INTER_AREA)
                        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        screen = buf.tobytes() if ok else None
            else:
                info = [f"v{VERSION}"]
            report.send(REPO, VERSION, kind, name, body, info, log_text, screen)
            self.last_report = time.time()
            self.q.put(("report_done", (True, "보냈어요. 고마워요!", win, send_btn)))
        except Exception as e:
            self.q.put(("report_done", (False, f"보내지 못했어요: {e}\n인터넷 연결을 확인하고 다시 시도해주세요.",
                                        win, send_btn)))

    def on_report_done(self, data):
        ok, msg, win, send_btn = data
        self.log("신고·건의: " + msg.splitlines()[0])
        try:
            if win.winfo_exists():
                send_btn.configure(state="normal", text="보내기")
                messagebox.showinfo("신고·건의", msg, parent=win)
                if ok:
                    win.destroy()
        except tk.TclError:
            pass

    # ---------- 고급 설정 ----------
    def build_advanced(self, parent):
        ttk.Label(parent, justify="left", wraplength=560, text=(
            "일치도: 화면의 그림이 버튼 그림과 얼마나 닮아야 '찾았다'고 볼지 정하는 기준이에요 (0.30~0.99).\n"
            "· 버튼을 못 찾고 지나가면 → 그 항목을 조금 낮추세요 (예: 0.75 → 0.70)\n"
            "· 엉뚱한 화면에서 버튼을 눌러버리면 → 그 항목을 조금 높이세요\n"
            "[지금 화면 점수 보기]를 누르면 지금 게임 화면에서 각 항목의 점수가 나와요. "
            "점수가 일치도 이상이면 초록색(찾음), 아래면 회색이에요. 바꾼 값은 [저장]을 눌러야 적용돼요."
        )).pack(fill="x", padx=8, pady=(8, 4))

        bar = ttk.Frame(parent)
        bar.pack(fill="x", padx=8, pady=4)
        ttk.Button(bar, text="지금 화면 점수 보기", command=self.adv_show_scores).pack(side="left", padx=3)
        ttk.Button(bar, text="저장", command=self.adv_save).pack(side="left", padx=3)
        ttk.Button(bar, text="모두 기본값으로", command=self.adv_reset).pack(side="left", padx=3)

        # 항목이 많아서 스크롤되는 표로 만듦
        box = ttk.Frame(parent)
        box.pack(fill="both", expand=True, padx=8, pady=4)
        canvas = tk.Canvas(box, height=380, highlightthickness=0)
        sb = ttk.Scrollbar(box, orient="vertical", command=canvas.yview)
        table = ttk.Frame(canvas)
        table.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=table, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        wheel = lambda e: canvas.yview_scroll(int(-e.delta / 120), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        for col, head in enumerate(("항목", "일치도", "기본값", "지금 화면 점수")):
            ttk.Label(table, text=head, font=("", 9, "bold")).grid(row=0, column=col, sticky="w", padx=6, pady=2)
        self.adv_vars, self.adv_score_labels = {}, {}
        names = [n for n in TEMPLATE_NAMES if n in self.vision.templates]
        names += [n for n in self.vision.templates if n not in TEMPLATE_NAMES]
        for i, name in enumerate(names, start=1):
            t = self.vision.templates[name]
            ttk.Label(table, text=TEMPLATE_NAMES.get(name, name)).grid(row=i, column=0, sticky="w", padx=6)
            var = tk.StringVar(value=f"{t['threshold']:.2f}")
            ttk.Spinbox(table, from_=0.30, to=0.99, increment=0.01, width=6, textvariable=var,
                        format="%.2f").grid(row=i, column=1, padx=6)
            ttk.Label(table, text=f"{t['default_threshold']:.2f}").grid(row=i, column=2, padx=6)
            lab = tk.Label(table, text="-", width=8, anchor="w")
            lab.grid(row=i, column=3, padx=6, sticky="w")
            self.adv_vars[name] = var
            self.adv_score_labels[name] = lab
        r = len(names) + 1
        ttk.Separator(table).grid(row=r, column=0, columnspan=4, sticky="ew", pady=4)
        ttk.Label(table, text="게이지 135 체크 노란 픽셀 수 (이상이면 달성)").grid(row=r + 1, column=0, sticky="w", padx=6)
        self.adv_gauge_var = tk.StringVar(value=str(self.cfg.get("gauge_yellow_min_px", 60)))
        ttk.Spinbox(table, from_=5, to=400, increment=5, width=6,
                    textvariable=self.adv_gauge_var).grid(row=r + 1, column=1, padx=6)
        ttk.Label(table, text="60").grid(row=r + 1, column=2, padx=6)
        self.adv_gauge_label = tk.Label(table, text="-", width=8, anchor="w")
        self.adv_gauge_label.grid(row=r + 1, column=3, padx=6, sticky="w")

    def adv_show_scores(self):
        frame = self.grab()
        if frame is None:
            return
        self.adv_save(quiet=True)
        norm, _, _ = Vision.normalize(frame)
        info = self.vision.analyze(norm)
        for name, lab in self.adv_score_labels.items():
            ok, score, _pt = info[name]
            lab.configure(text=f"{score:.2f}", fg=("#1a8f2e" if ok else "#888888"))
        y = info["_yellow135"]
        self.adv_gauge_label.configure(text=str(y),
                                       fg=("#1a8f2e" if y >= self.cfg["gauge_yellow_min_px"] else "#888888"))
        self.log("고급 설정: 지금 화면의 점수를 표시했어요.")

    def adv_save(self, quiet=False):
        th = {}
        for name, var in self.adv_vars.items():
            try:
                v = round(min(0.99, max(0.30, float(var.get()))), 2)
            except ValueError:
                v = self.vision.templates[name]["default_threshold"]
            var.set(f"{v:.2f}")
            if abs(v - self.vision.templates[name]["default_threshold"]) > 1e-6:
                th[name] = v
        self.cfg["thresholds"] = th
        try:
            self.cfg["gauge_yellow_min_px"] = max(1, int(float(self.adv_gauge_var.get())))
        except ValueError:
            pass
        self.adv_gauge_var.set(str(self.cfg["gauge_yellow_min_px"]))
        self.vision.set_thresholds(th)        # 진행 중이어도 바로 적용
        save_config(self.cfg)
        if not quiet:
            changed = ", ".join(f"{TEMPLATE_NAMES.get(k, k)} {v:.2f}" for k, v in th.items()) or "모두 기본값"
            self.log(f"고급 설정 저장: {changed} / 게이지 기준 {self.cfg['gauge_yellow_min_px']}")

    def adv_reset(self):
        for name, var in self.adv_vars.items():
            var.set(f"{self.vision.templates[name]['default_threshold']:.2f}")
        self.adv_gauge_var.set("60")
        self.adv_save()

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

    def mode_name(self, mode=None):
        mode = mode or self.cfg.get("mode", "full")
        if mode == "rc_custom":
            k = rc_kind_for(self.cfg, datetime.now().date())
            return f"랭챌 요일별 갱신 (오늘은 {'스갱' if k == 'star' else '포갱'})"
        return {"full": "리그 풀플레이", "highlight": "리그 하이라이트",
                "rc_point": "랭챌 포갱", "rc_star": "랭챌 스갱"}.get(mode, mode)

    def change_mode(self):
        self.cfg["mode"] = self.mode_var.get()
        save_config(self.cfg)
        self.update_today_label()
        where = ("랭킹챌린지 로비에서 [▶ 시작]을 누르세요" if self.cfg["mode"].startswith("rc_")
                 else "리그 메인/플레이 방식 선택 화면이나 경기 중에 [▶ 시작]을 누르세요")
        self.log(f"선택: {self.mode_name()} ({where})")

    def change_week(self):
        self.cfg["rc_week"] = ["star" if v.get() == "스갱" else "point" for v in self.week_vars]
        save_config(self.cfg)
        self.update_today_label()
        self.log("요일별 갱신: " + " ".join(f"{d}{v.get()[0]}" for d, v in zip(WEEK, self.week_vars)))

    def update_today_label(self):
        day = datetime.now().date()
        k = rc_kind_for(dict(self.cfg, mode="rc_custom"), day)
        self.today_var.set(f"오늘({WEEK[day.weekday()]}): {'스갱' if k == 'star' else '포갱'}")

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
            self.log(f"v{v}을 받았어요. 프로그램을 껐다가 컴프야 매크로.bat으로 다시 켜면 적용돼요.")
            messagebox.showinfo("업데이트", f"v{v}을 받았어요.\n프로그램을 껐다가 컴프야 매크로.bat으로 다시 켜면 적용돼요.")
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
                elif kind == "report_done":
                    self.on_report_done(data)
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
        rc = self.cfg["mode"].startswith("rc_")
        self.games_label.set("끝낸 연속 경기" if rc else "완료한 경기")
        self.sim_label.set("갱신 횟수" if rc else "시뮬레이션 전환")
        self.log("시작: " + self.mode_name())
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
        brain = make_brain(self.cfg, self.vision)
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
