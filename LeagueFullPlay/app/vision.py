"""화면 인식: 템플릿 매칭 + 미션 게이지 135 체크 판정

모든 화면은 768 x 1366 (9:16) 크기로 맞춘 뒤 비교합니다.
템플릿 좌표도 이 기준 좌표입니다.
"""
import json
import os

import cv2
import numpy as np

NORM_W = 768
NORM_H = 1366
SCALES = (1.0, 0.96, 1.04)


def imread_any(path):
    """한글 등이 들어간 경로도 읽기 (cv2.imread 는 윈도우에서 한글 경로를 못 읽음)"""
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


class Vision:
    def __init__(self, template_dir):
        with open(os.path.join(template_dir, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.check_box = meta.pop("_gauge_135_check_box")
        self.select_cards = meta.pop("_select_cards", {})
        self.rc = meta.pop("_rc", {})
        self.templates = {}
        for name, info in meta.items():
            img = imread_any(os.path.join(template_dir, name + ".png"))
            if img is None:
                raise FileNotFoundError(f"템플릿 파일이 없습니다: {name}.png")
            variants = []
            for s in SCALES:
                if s == 1.0:
                    variants.append(img)
                else:
                    variants.append(cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA))
            self.templates[name] = {
                "imgs": variants,
                "box": info["box"],
                "margin": info["margin"],
                "search": info.get("search"),          # 있으면 이 넓은 영역 전체에서 찾음
                "threshold": info["threshold"],
                "default_threshold": info["threshold"],
            }

    def set_thresholds(self, overrides):
        """고급 설정에서 바꾼 일치도 적용 ({이름: 값}). 목록에 없는 건 기본값으로"""
        overrides = overrides or {}
        for name, t in self.templates.items():
            try:
                v = float(overrides.get(name, t["default_threshold"]))
            except (TypeError, ValueError):
                v = t["default_threshold"]
            t["threshold"] = min(0.99, max(0.3, v))

    @staticmethod
    def normalize(frame):
        """기기 화면을 768x1366으로 맞춤. (정규화 이미지, x배율, y배율) 반환"""
        h, w = frame.shape[:2]
        norm = cv2.resize(frame, (NORM_W, NORM_H), interpolation=cv2.INTER_AREA)
        return norm, w / NORM_W, h / NORM_H

    def match(self, norm, name):
        """(점수, 중심좌표(정규화 기준)) 반환"""
        t = self.templates[name]
        if t["search"]:
            rx1, ry1, rx2, ry2 = t["search"]
        else:
            x1, y1, x2, y2 = t["box"]
            m = t["margin"]
            rx1, ry1 = max(0, x1 - m), max(0, y1 - m)
            rx2, ry2 = min(NORM_W, x2 + m), min(NORM_H, y2 + m)
        roi = norm[ry1:ry2, rx1:rx2]
        best, best_pt = -1.0, None
        for tpl in t["imgs"]:
            th, tw = tpl.shape[:2]
            if roi.shape[0] < th or roi.shape[1] < tw:
                continue
            res = cv2.matchTemplate(roi, tpl, cv2.TM_CCOEFF_NORMED)
            _, mx, _, loc = cv2.minMaxLoc(res)
            if mx > best:
                best = mx
                best_pt = (rx1 + loc[0] + tw // 2, ry1 + loc[1] + th // 2)
        return float(best), best_pt

    def found(self, norm, name):
        score, pt = self.match(norm, name)
        return score >= self.templates[name]["threshold"], score, pt

    def gauge_135_yellow(self, norm):
        """135 칸 위치의 노란 체크 픽셀 수"""
        x1, y1, x2, y2 = self.check_box
        roi = norm[y1:y2, x1:x2].astype(np.int16)
        b, g, r = roi[..., 0], roi[..., 1], roi[..., 2]
        mask = (r > 190) & (g > 160) & (b < 100) & (r - b > 110)
        return int(mask.sum())

    def selected_card(self, norm):
        """플레이 방식 선택 화면에서 하늘색 테두리로 선택된 카드 ('highlight' / 'full' / None)"""
        best, best_n = None, 0
        for name, c in self.select_cards.items():
            x1, y1, x2, y2 = c["strip"]
            roi = norm[y1:y2, x1:x2].astype(np.int16)
            b, g, r = roi[..., 0], roi[..., 1], roi[..., 2]
            n = int(((b > 200) & (g > 180) & (r < 120)).sum())
            if n > best_n:
                best, best_n = name, n
        return best if best_n >= 300 else None

    # ---------- 랭킹챌린지 (랭챌분석기와 같은 판정, 768x1366 기준으로 옮김) ----------
    @staticmethod
    def _rgb(norm, box):
        x1, y1, x2, y2 = box
        a = norm[y1:y2, x1:x2].astype(np.int32)
        return a[..., 2], a[..., 1], a[..., 0]          # r, g, b

    def rc_refresh_kind(self, norm):
        """로비 갱신 버튼: 'free'(무료 n/3) / 'point'(P) / 'star'(스타) / 'done'(금일 갱신 완료)"""
        r, g, b = self._rgb(norm, self.rc["refresh_box"])
        mn = np.minimum(np.minimum(r, g), b)
        white = float((mn > 215).mean())
        gray = float(((mn > 160) & (mn < 205)).mean())
        if white < 0.15 and gray > 0.35:
            return "done"
        r, g, b = self._rgb(norm, self.rc["free_box"])
        mx = np.maximum(np.maximum(r, g), b)
        mn = np.minimum(np.minimum(r, g), b)
        if float(((mx - mn < 20) & (mx > 100) & (mx < 170)).mean()) > 0.4:
            return "free"
        r, g, b = self._rgb(norm, self.rc["refresh_icon_box"])
        yellow = int(((r > 200) & (g > 150) & (b < 90)).sum())
        blue = int(((b > 110) & (b > r + 40) & (r < 110)).sum())
        if yellow > 28:
            return "star"
        if blue > 42:
            return "point"
        return "free"

    def rc_popup_kind(self, norm):
        """즉시 갱신 창 결제 버튼에 스타(노란색)가 있으면 'star', 아니면 'point'"""
        r, g, b = self._rgb(norm, self.rc["refresh_pay_box"])
        return "star" if int(((r > 200) & (g > 150) & (b < 90)).sum()) > 28 else "point"

    def rc_cont_go_blue(self, norm):
        """연속 경기 창의 [경기 진행] 버튼이 파란색(누를 수 있음)인지"""
        r, g, b = self._rgb(norm, self.rc["cont_go_box"])
        return float(((b > 180) & (r < 60) & (g > 40) & (g < 140)).mean()) > 0.2

    def rc_rows(self, norm):
        """로비 5줄: 각 줄이 '치름'(True) / '안 치름'(False).
        안 치른 줄에는 밝은 PLAY BALL 버튼, 치른 줄에는 어두운 바탕의 승/패 결과가 있음"""
        th = self.rc.get("playball_bright", 0.35)
        out = []
        for row in self.rc["rows"]:
            r, g, b = self._rgb(norm, row["playball"])
            bright = float((np.minimum(np.minimum(r, g), b) > 190).mean())
            out.append(bright < th)
        return out

    def analyze(self, norm):
        """화면에서 필요한 모든 정보를 한 번에 계산"""
        info = {}
        for name in self.templates:
            ok, score, pt = self.found(norm, name)
            info[name] = (ok, score, pt)
        info["_yellow135"] = self.gauge_135_yellow(norm)
        info["_selected"] = self.selected_card(norm) if info.get("select_title", (False,))[0] else None
        if self.rc:
            lobby = info.get("rc_lobby", (False,))[0]
            info["_rc_kind"] = self.rc_refresh_kind(norm) if lobby else None
            info["_rc_rows"] = self.rc_rows(norm) if lobby else None
            info["_rc_popup_kind"] = (self.rc_popup_kind(norm)
                                      if info.get("rc_refresh_popup", (False,))[0] else None)
            info["_rc_go_blue"] = (self.rc_cont_go_blue(norm)
                                   if info.get("rc_cont_popup", (False,))[0] else None)
        return info
