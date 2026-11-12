"""리그 풀플레이 동작 로직

Brain: 화면 인식 결과를 보고 무엇을 누를지 결정 (ADB와 분리되어 있어 테스트 가능)
Bot:   ADB로 캡처 -> Brain 판단 -> 탭, 을 반복하는 작업 스레드
"""
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

        if has("alert_sim"):
            screen = "알림(시뮬레이션 전환)"
            if self.need_sim and not self.sim_done:
                if self._ready("alert_ok", now, cd):
                    taps.append(self._pt(info, "alert_ok", (507, 856)))
                    self.sim_done = True
                    self.sim_seq_since = None
                    self.sim_switches += 1
                    logs.append("시뮬레이션 전환 확인 → 경기 끝까지 자동 진행")
        elif has("coach_title"):
            screen = "코치의 조언"
            if self._ready("coach", now, cd):
                taps.append(self._pt(info, "coach_replace", (664, 312)))
                logs.append("투수 체력 저하 → 교체")
        elif has("menu_sim") or has("menu_continue"):
            screen = "일시정지 메뉴"
            if self.need_sim and not self.sim_done and has("menu_sim"):
                if self._ready("menu_sim", now, cd):
                    taps.append(self._pt(info, "menu_sim", (384, 954)))
                    if self.sim_seq_since is None:
                        self.sim_seq_since = now
                    logs.append("시뮬레이션 전환 선택")
            else:
                if self.need_sim and not self.sim_done:
                    # 메뉴에 시뮬레이션 전환이 없음 = 이미 전환된 경기
                    self.sim_done = True
                    self.sim_seq_since = None
                if self._ready("menu_continue", now, cd):
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


class Bot(threading.Thread):
    def __init__(self, adb, vision, cfg, on_log, on_status, on_stop):
        super().__init__(daemon=True)
        self.adb, self.vision, self.cfg = adb, vision, cfg
        self.on_log, self.on_status, self.on_stop = on_log, on_status, on_stop
        self.brain = Brain(cfg)
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
                norm, sx, sy = Vision.normalize(frame)
                info = self.vision.analyze(norm)
                now = time.time()
                screen, taps, logs, neutral, stop = self.brain.step(info, now)

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

                if stop:
                    reason = stop
                    break

                for pt in taps:
                    x, y = self.to_device(pt, sx, sy)
                    self.adb.tap(x, y)

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
