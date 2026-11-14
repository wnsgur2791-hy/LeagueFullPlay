"""리그 풀플레이 동작 로직

Brain: 화면 인식 결과를 보고 무엇을 누를지 결정 (ADB와 분리되어 있어 테스트 가능)
Bot:   ADB로 캡처 -> Brain 판단 -> 탭, 을 반복하는 작업 스레드
"""
import gc
import os
import threading
import time
from datetime import datetime

import cv2

from vision import Vision


class Brain:
    """화면 인식 결과 -> 누를 곳 결정.

    mode: 'full'(풀플레이) / 'highlight'(하이라이트) - 플레이 방식 선택 화면에서 고를 카드
    경기 화면 자체로 어느 방식인지 알 수 있어서, 경기 중에는 보이는 화면대로 처리함
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.games_done = 0
        self.sim_switches = 0
        self.last_action = {}
        self.kind = None           # 지금 경기 종류: 'full' / 'highlight' (빈 곳 탭 위치에 씀)
        self.reset_game()

    def reset_game(self):
        self.need_sim = False      # 시뮬레이션 전환 필요 (풀: 135 달성 / 하이라이트: 바로)
        self.sim_done = False      # 이번 경기에서 이미 시뮬레이션으로 전환함
        self.sim_seq_since = None  # 일시정지~확인 진행 중이면 시작 시각
        self.yellow_hits = 0
        self.menu_no_sim = 0       # 메뉴에서 '시뮬레이션 전환' 칸이 안 보인 연속 횟수

    def _ready(self, key, now, cooldown):
        if now - self.last_action.get(key, -1e9) >= cooldown:
            self.last_action[key] = now
            return True
        return False

    @staticmethod
    def _pt(info, name, fallback=None):
        ok, _score, pt = info[name]
        return pt if ok and pt else fallback

    def _neutral_point(self):
        if self.kind == "highlight":
            return self.cfg["neutral_tap_point_highlight"]
        return self.cfg["neutral_tap_point"]

    def step(self, info, now):
        """반환: (화면 이름, [탭할 좌표(정규화 기준)], [로그], 빈곳탭 좌표 또는 None, 'stop' 사유 또는 None)"""
        cfg = self.cfg
        taps, logs = [], []
        stop = None
        neutral_ok = True
        cd = cfg["action_cooldown_sec"]
        mode = cfg.get("mode", "full")

        # 시뮬레이션 전환 절차가 너무 오래 걸리면 처음부터 다시
        if self.sim_seq_since and now - self.sim_seq_since > cfg["sim_switch_timeout_sec"]:
            logs.append("시뮬레이션 전환이 완료되지 않아 다시 시도합니다.")
            self.sim_seq_since = None

        def has(name):
            return info[name][0]

        alert = has("alert_sim") or (has("alert_ok") and (has("menu_sim") or has("menu_continue")))
        if alert:
            screen = "알림(시뮬레이션 전환)"
            # 우리가 전환하려던 경기면 확인 (이미 전환됐다고 기록돼 있어도 창이 떠 있으면 누름)
            if self.need_sim and self._ready("alert_ok", now, cd):
                taps.append(self._pt(info, "alert_ok", (507, 856)))
                if not self.sim_done:
                    self.sim_switches += 1
                self.sim_done = True
                self.sim_seq_since = None
                self.menu_no_sim = 0
                logs.append("시뮬레이션 전환 확인 → 경기 끝까지 자동 진행")
        elif has("coach_title"):
            screen = "코치의 조언"
            if self._ready("coach", now, cd):
                taps.append(self._pt(info, "coach_replace", (664, 312)))
                logs.append("투수 체력 저하 → 교체")
        elif has("menu_sim") or has("menu_continue"):
            screen = "일시정지 메뉴"
            if self.need_sim and not self.sim_done and has("menu_sim"):
                self.menu_no_sim = 0
                if self._ready("menu_sim", now, cd):
                    taps.append(self._pt(info, "menu_sim", (384, 954)))
                    if self.sim_seq_since is None:
                        self.sim_seq_since = now
                    logs.append("시뮬레이션 전환 선택")
            elif self.need_sim and not self.sim_done:
                # '시뮬레이션 전환' 칸이 안 보임: 알림창이 떠오르는 중일 수 있으니 기다림.
                # 여러 번 연속으로(약 3초) 안 보일 때만 '이미 전환된 경기'로 보고 계속 하기
                self.menu_no_sim += 1
                if self.menu_no_sim >= 8:
                    self.sim_done = True
                    self.sim_seq_since = None
                    if self._ready("menu_continue", now, cd):
                        taps.append(self._pt(info, "menu_continue", (541, 1149)))
                        logs.append("메뉴에 시뮬레이션 전환이 없어 이미 전환된 경기로 보고 계속 하기")
            elif self._ready("menu_continue", now, cd):
                taps.append(self._pt(info, "menu_continue", (541, 1149)))
                logs.append("일시정지 메뉴가 열려 있어 계속 하기")
        elif has("round_again"):
            screen = "라운드 결과"
            if self._ready("round_again", now, cfg["round_cooldown_sec"]):
                self.games_done += 1
                logs.append(f"경기 완료 ({self.games_done}경기째)")
                limit = int(cfg.get("max_games", 0) or 0)
                if limit and self.games_done >= limit:
                    stop = f"설정한 {limit}경기를 모두 진행했습니다."
                else:
                    taps.append(self._pt(info, "round_again", (220, 1256)))
                    logs.append("한 번 더 하기")
                self.reset_game()
        elif has("mvp_title"):
            screen = "MVP·반복과제 보상"
            if self._ready("mvp_next", now, cd):
                taps.append(self._pt(info, "mvp_next", (384, 1274)))
        elif has("result_title"):
            screen = "경기 결과"
            if self._ready("result_next", now, cd):
                taps.append(self._pt(info, "result_next", (568, 1273)))
        elif has("select_title"):
            screen = "플레이 방식 선택"
            neutral_ok = False                       # 아래 '뒤로가기'가 눌리지 않게
            want = "highlight" if mode == "highlight" else "full"
            sel = info.get("_selected")
            if sel != want:
                if self._ready("select_card", now, cd):
                    card = self.cfg_card(want)
                    taps.append(card)
                    logs.append(f"{'하이라이트' if want == 'highlight' else '풀플레이'} 선택")
            elif self._ready("select_start", now, 3.0):
                taps.append(self._pt(info, "select_start", (384, 1142)))
                logs.append("START")
                self.reset_game()
                self.kind = want
        elif has("main_title"):
            screen = "리그 메인"
            neutral_ok = False
            if self._ready("main_playball", now, 3.0):
                taps.append(self._pt(info, "main_playball", (384, 1094)))
                logs.append("리그 메인 → PLAY BALL")
        elif has("hl_title") and has("hl_skip"):
            screen = "하이라이트 직접 플레이"
            self.kind = "highlight"
            if self._ready("hl_skip", now, cd):
                taps.append(self._pt(info, "hl_skip", (204, 1299)))
                logs.append("하이라이트 직접 플레이 → 건너 뛰기")
        elif has("hl_view"):
            screen = "하이라이트 진행"
            self.kind = "highlight"
            if not self.sim_done:
                if not self.need_sim:
                    self.need_sim = True
                    logs.append("하이라이트 경기 → 바로 시뮬레이션으로 전환합니다.")
                if has("hl_pause"):
                    self._try_pause(info, now, taps, logs, "hl_pause", None)
        elif has("catcher_lead"):
            screen = "투구"
            self.kind = "full"
            if self.need_sim and not self.sim_done:
                self._try_pause(info, now, taps, logs, "pause_btn", (736, 32))
            elif self._ready("catcher_lead", now, cfg["pitch_cooldown_sec"]):
                taps.append(self._pt(info, "catcher_lead", (642, 1085)))
        elif has("outs"):
            screen = "경기 진행"
            self.kind = "full"
            # 미션 게이지 135 체크 확인 (자동 타격 중에만 보임)
            if not self.need_sim and not self.sim_done and has("gauge_zero"):
                screen = "경기 진행(게이지 보임)"
                if info["_yellow135"] >= cfg["gauge_yellow_min_px"]:
                    self.yellow_hits += 1
                    if self.yellow_hits >= 2:
                        self.need_sim = True
                        logs.append("미션 게이지 135 달성 확인 → 시뮬레이션으로 전환합니다.")
                else:
                    self.yellow_hits = 0
            if self.need_sim and not self.sim_done:
                self._try_pause(info, now, taps, logs, "pause_btn", (736, 32))
            elif not self.sim_done:
                # 투구 직접 플레이가 꺼져 있으면 켜기
                off, on = info["pitch_off"], info["pitch_on"]
                if off[0] and off[1] > on[1] + 0.05 and self._ready("pitch_toggle", now, 4.0):
                    taps.append(off[2])
                    logs.append("투구가 OFF라서 ON으로 바꿉니다.")
        else:
            screen = "알 수 없음"

        # 일시정지 → 메뉴 → 알림 확인 중에는 빈 곳 탭을 멈춤 (바깥 탭으로 창이 닫히지 않게)
        if self.sim_seq_since and cfg["pause_neutral_tap_during_sim_switch"]:
            neutral_ok = False
        taps = [t for t in taps if t]
        return screen, taps, logs, (self._neutral_point() if neutral_ok else None), stop

    def cfg_card(self, want):
        cards = self.cfg.get("_select_cards") or {}
        if want in cards:
            return tuple(cards[want]["tap"])
        return (384, 556) if want == "highlight" else (607, 556)

    def _try_pause(self, info, now, taps, logs, name, fallback):
        if self.sim_seq_since is None and self._ready("pause", now, 3.0):
            ok, _s, pt = info[name]
            pt = pt if ok and pt else fallback
            if pt:
                taps.append(pt)
                self.sim_seq_since = now
                logs.append("일시정지")


class RcBrain:
    """랭킹챌린지 간편 매크로: 로비 → 연속 경기 → 결과 확인 → 갱신 → 반복 (선발 배치는 건드리지 않음)

    갱신 방식(kind): 'point' = 무료·포인트 갱신까지 / 'star' = 스타 갱신까지 전부
    멈추는 때
      · 포갱: 새 상대 목록인데 다음 갱신이 스타 → 이 5경기는 치르지 않고 남겨 둠
      · 스갱: 새 상대 목록인데 '금일 갱신 완료' → 이 5경기는 남겨 둠
      · 자정(날짜가 바뀜)이 되면: 진행 중인 연속 경기는 끝까지 보고, 로비로 오면 멈춤
    절대 누르지 않는 것: 포갱에서 스타 결제, [연속 경기 종료], 구매
    """
    KIND_NAME = {"free": "무료", "point": "포인트", "star": "스타", "done": "금일 갱신 완료"}

    def __init__(self, cfg, today=None):
        self.cfg = cfg
        self.today = today or datetime.now().date()
        self.kind = rc_kind_for(cfg, self.today)
        self.games_done = 0        # 끝낸 연속 경기 수 (화면의 '완료' 칸)
        self.sim_switches = 0      # 갱신 횟수 (화면의 두 번째 칸)
        self.last_action = {}
        self.series_since = None   # 연속 경기 진행 시작 시각
        self.after_series = None   # 결과 확인을 누른 시각 (승급 화면 등 넘기기)
        self.after_taps = 0
        self.started_logged = False
        self.last_free_tap = None
        self.popup_seen = None     # (처음 본 시각, 판단) - 즉시 갱신 창
        self.go_grey_since = None  # 연속 경기 창의 [경기 진행]이 회색으로 보이기 시작한 시각

    _ready = Brain._ready

    def _p(self, name):
        return tuple(RC_POINTS[name])

    def step(self, info, now, today=None):
        cfg = self.cfg
        taps, logs = [], []
        stop = None
        if not self.started_logged:
            self.started_logged = True
            logs.append(f"랭챌 매크로: {'스갱(스타 갱신까지)' if self.kind == 'star' else '포갱(포인트 갱신까지)'}")
        midnight = (today or datetime.now().date()) != self.today

        def has(name):
            return info.get(name, (False,))[0]

        if not has("rc_cont_popup"):
            self.go_grey_since = None
        if not has("rc_refresh_popup"):
            self.popup_seen = None
        if has("rc_refresh_popup"):
            screen = "즉시 갱신 창"
            pk = info.get("_rc_popup_kind") or "point"
            # 창이 떠오르는 중에는 스타 아이콘이 흐려서 포인트로 보일 수 있음 → 같은 판단이 1초 이상 유지될 때만 결제
            if self.popup_seen is None or self.popup_seen[1] != pk:
                self.popup_seen = (now, pk)
            settled = now - self.popup_seen[0] >= 1.0
            if pk == "star" and self.kind != "star":
                settled = True                   # 스타가 한 번이라도 보이면 바로 닫기
            if not settled:
                pass
            elif midnight or (pk == "star" and self.kind != "star"):
                if self._ready("rc_close", now, 3.0):
                    taps.append(self._p("refresh_close"))
                    stop = ("자정이 되어 갱신하지 않고 멈췄어요." if midnight else
                            "즉시 갱신 창에 스타가 표시돼서 누르지 않고 멈췄어요. (포갱 모드)")
            elif self._ready("rc_pay", now, 4.0):
                taps.append(self._p("refresh_pay"))
                if self.last_free_tap and now - self.last_free_tap < 15:
                    logs.append(f"(무료 갱신으로 보였는데 결제 창이 떴어요) {self.KIND_NAME[pk]} 갱신 결제")
                else:
                    self.sim_switches += 1
                    logs.append(f"{self.KIND_NAME[pk]} 갱신 결제 ({self.sim_switches}번째 갱신)")
                self.last_free_tap = None
        elif has("rc_series_result"):
            screen = "연속 경기 결과"
            if self._ready("rc_ok", now, 3.0):
                taps.append(self._p("series_ok"))
                if self.series_since is not None:
                    self.games_done += 1
                    logs.append(f"연속 경기 끝 ({(now - self.series_since) / 60:.1f}분) → 확인 "
                                f"({self.games_done}번째)")
                self.series_since = None
                self.after_series = now
                self.after_taps = 0
        elif has("rc_cont_popup"):
            screen = "연속 경기 창"
            if info.get("_rc_go_blue") is False:
                # 창이 떠오르는 중이면 버튼이 아직 파랗지 않음 → 파래질 때까지 기다림 (8초 넘게 회색이면 멈춤)
                if self.go_grey_since is None:
                    self.go_grey_since = now
                elif now - self.go_grey_since > 8:
                    stop = "연속 경기 창의 [경기 진행] 버튼이 8초 넘게 눌리지 않는 상태라 멈췄어요."
            else:
                self.go_grey_since = None
                if self._ready("rc_go", now, 4.0):
                    taps.append(self._p("cont_go"))
                    self.series_since = now
                    logs.append("경기 진행 → 연속 경기 진행 중 (보통 5분)")
        elif has("rc_lobby"):
            screen = "랭챌 로비"
            self.after_series = None
            rows = info.get("_rc_rows") or [False] * 5
            kind = info.get("_rc_kind") or "free"
            all_played, none_played = all(rows), not any(rows)
            blocked = kind == "done" or (kind == "star" and self.kind != "star")
            limit = int(cfg.get("max_games", 0) or 0)
            if midnight:
                stop = "자정이 되어 멈췄어요."
            elif limit and self.games_done >= limit:
                stop = f"설정한 연속 경기 {limit}번을 모두 진행했어요."
            elif not all_played:
                if none_played and blocked:
                    stop = ("오늘 갱신을 모두 써서(금일 갱신 완료) 지금 5경기는 남겨 두고 멈췄어요."
                            if kind == "done" else
                            "다음 갱신이 스타라서 지금 5경기는 남겨 두고 멈췄어요. (포갱 모드)")
                elif self._ready("rc_cont", now, 5.0):
                    taps.append(self._p("cont_btn"))
                    logs.append(f"남은 경기 {rows.count(False)}개 → 연속 경기")
            else:
                if kind == "done":
                    stop = "오늘 갱신을 모두 썼어요 (금일 갱신 완료)."
                elif kind == "star" and self.kind != "star":
                    stop = "다음 갱신이 스타라서 멈췄어요. (포갱 모드)"
                elif self._ready("rc_refresh", now, 6.0):
                    taps.append(self._p("refresh_btn"))
                    if kind == "free":
                        self.sim_switches += 1
                        self.last_free_tap = now
                        logs.append(f"5경기 완료 → 무료 갱신 ({self.sim_switches}번째 갱신)")
                    else:
                        logs.append(f"5경기 완료 → {self.KIND_NAME[kind]} 갱신 버튼")
        else:
            if self.series_since is not None:
                screen = "연속 경기 진행 중"
                if now - self.series_since > cfg.get("rc_series_timeout_sec", 900):
                    stop = "연속 경기가 15분 안에 끝나지 않아서 멈췄어요."
            elif self.after_series is not None and now - self.after_series < 30:
                screen = "연속 경기 뒤 화면"
                # 승급 화면 등: 화면 가운데를 눌러 넘김 (랭챌분석기와 같은 방식)
                if self.after_taps < 5 and self._ready("rc_center", now, 3.0):
                    taps.append(self._p("center"))
                    self.after_taps += 1
                    logs.append("로비가 아닌 화면 → 화면 가운데를 눌러 넘겨요 (승급 화면일 수 있어요)")
            else:
                # 연속 경기 도중에 시작한 경우 등: 진행 중인 연속 경기로 보고 기다림 (최대 15분)
                self.series_since = now
                screen = "연속 경기 진행 중"
                logs.append("로비가 아닌 화면 → 진행 중인 연속 경기로 보고 기다려요")
        # 랭챌은 연속 경기 중 [연속 경기 종료]가 눌리면 안 돼서 빈 곳 탭을 하지 않음
        return screen, taps, logs, None, stop


RC_POINTS = {}
WEEK = ["월", "화", "수", "목", "금", "토", "일"]


def rc_kind_for(cfg, day):
    """모드와 요일로 오늘의 갱신 방식 ('point' / 'star')"""
    mode = cfg.get("mode")
    if mode == "rc_star":
        return "star"
    if mode == "rc_custom":
        week = cfg.get("rc_week") or ["point"] * 7
        return "star" if week[day.weekday()] == "star" else "point"
    return "point"


def make_brain(cfg, vision=None):
    if vision is not None and vision.rc:
        RC_POINTS.update(vision.rc.get("points", {}))
    if str(cfg.get("mode", "")).startswith("rc_"):
        return RcBrain(cfg)
    return Brain(cfg)


class Bot(threading.Thread):
    def __init__(self, adb, vision, cfg, on_log, on_status, on_stop):
        super().__init__(daemon=True)
        self.adb, self.vision, self.cfg = adb, vision, cfg
        self.on_log, self.on_status, self.on_stop = on_log, on_status, on_stop
        self.brain = make_brain(cfg, vision)
        self._stop_evt = threading.Event()
        self.last_frame = None

    def stop(self):
        self._stop_evt.set()

    def to_device(self, pt, sx, sy):
        return pt[0] * sx, pt[1] * sy

    def run(self):
        cfg = self.cfg
        last_neutral = 0.0
        last_known = time.time()
        last_screen = None
        reason = "정지 버튼을 눌렀습니다."
        errors = 0
        self.on_log("자동 진행을 시작합니다.")
        try:
            while not self._stop_evt.is_set():
                loop_start = time.time()
                try:
                    frame = self.adb.screencap()
                    errors = 0
                except Exception as e:  # 일시적 ADB 오류는 몇 번 재시도
                    errors += 1
                    self.on_log(f"화면 캡처 오류({errors}/5): {e}")
                    if errors >= 5:
                        reason = "ADB 연결이 계속 실패해서 멈췄습니다."
                        break
                    time.sleep(2)
                    continue
                self.last_frame = frame
                try:
                    norm, sx, sy = Vision.normalize(frame)
                    info = self.vision.analyze(norm)
                    now = time.time()
                    screen, taps, logs, neutral, stop = self.brain.step(info, now)
                    errors = 0
                except Exception as e:   # 메모리 부족 같은 일시적 오류는 잠깐 쉬고 다시
                    errors += 1
                    self.on_log(f"화면 분석 오류({errors}/5): {e}")
                    frame = norm = info = None
                    gc.collect()
                    if errors >= 5:
                        reason = f"화면 분석 오류가 계속돼서 멈췄습니다: {e}"
                        break
                    self._stop_evt.wait(3)
                    continue

                for l in logs:
                    self.on_log(l)
                if screen != last_screen:
                    last_screen = screen
                self.on_status(screen, self.brain.games_done, self.brain.sim_switches)

                if screen != "알 수 없음":
                    last_known = now
                elif now - last_known > cfg["stuck_timeout_sec"]:
                    path = save_capture(frame, "멈춤")
                    reason = (f"{cfg['stuck_timeout_sec']}초 동안 아는 화면이 나오지 않아 멈췄습니다. "
                              f"(볼 소진·리그 종료·새 팝업일 수 있음) 화면 저장: {path}")
                    break

                for pt in taps:
                    x, y = self.to_device(pt, sx, sy)
                    self.adb.tap(x, y)

                if stop:
                    reason = stop
                    break

                # 컷씬 스킵용 빈 곳 탭
                if not taps and neutral and now - last_neutral >= cfg["neutral_tap_interval_sec"]:
                    x, y = self.to_device(neutral, sx, sy)
                    self.adb.tap(x, y)
                    last_neutral = time.time()

                elapsed = time.time() - loop_start
                wait = cfg["loop_interval_sec"] - elapsed
                if wait > 0:
                    self._stop_evt.wait(wait)
        except Exception as e:
            reason = f"오류로 멈췄습니다: {e}"
        self.on_stop(reason)


def save_capture(frame, tag="화면"):
    os.makedirs("captures", exist_ok=True)
    path = os.path.join("captures", f"{tag}_{datetime.now():%Y%m%d_%H%M%S}.png")
    ok, buf = cv2.imencode(".png", frame)  # 한글 경로 대응
    if ok:
        buf.tofile(path)
    return path
