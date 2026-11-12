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

    def analyze(self, norm):
        """화면에서 필요한 모든 정보를 한 번에 계산"""
        info = {}
        for name in self.templates:
            ok, score, pt = self.found(norm, name)
            info[name] = (ok, score, pt)
        info["_yellow135"] = self.gauge_135_yellow(norm)
        info["_selected"] = self.selected_card(norm) if info.get("select_title", (False,))[0] else None
        return info
