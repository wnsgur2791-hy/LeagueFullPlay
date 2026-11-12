"""ADB 연결: LDPlayer 화면 캡처와 탭"""
import os
import shutil
import struct
import subprocess

import cv2
import numpy as np

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

COMMON_ADB_PATHS = [
    r"C:\LDPlayer\LDPlayer9\adb.exe",
    r"C:\LDPlayer\LDPlayer4.0\adb.exe",
    r"D:\LDPlayer\LDPlayer9\adb.exe",
    r"D:\LDPlayer\LDPlayer4.0\adb.exe",
    r"C:\Program Files\LDPlayer\LDPlayer9\adb.exe",
    r"C:\Program Files (x86)\LDPlayer\LDPlayer9\adb.exe",
]


class AdbError(Exception):
    pass


def find_adb(configured=""):
    if configured and os.path.isfile(configured):
        return configured
    for p in COMMON_ADB_PATHS:
        if os.path.isfile(p):
            return p
    found = shutil.which("adb")
    if found:
        return found
    raise AdbError(
        "adb.exe를 찾지 못했습니다.\n"
        r"예: C:\LDPlayer\LDPlayer9\adb.exe"
    )


class Adb:
    def __init__(self, adb_path="", serial=""):
        self.adb = find_adb(adb_path)
        self.serial = serial
        self.size = None  # (가로, 세로)

    def _run(self, args, timeout=15, binary=False):
        cmd = [self.adb] + (["-s", self.serial] if self.serial else []) + args
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                               creationflags=CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise AdbError("ADB 응답이 없습니다 (시간 초과): " + " ".join(args))
        if r.returncode != 0:
            raise AdbError((r.stderr or r.stdout).decode("utf-8", "ignore").strip()
                           or "ADB 명령 실패: " + " ".join(args))
        return r.stdout if binary else r.stdout.decode("utf-8", "ignore")

    def connect(self):
        """기기를 찾아 연결하고 기기 이름을 반환"""
        saved = self.serial
        self.serial = ""
        out = self._run(["devices"])
        devices = [l.split()[0] for l in out.splitlines()[1:] if l.strip().endswith("device")]
        if not devices:
            # LDPlayer 기본 포트로 한 번 시도
            for addr in ("127.0.0.1:5555", "emulator-5554"):
                try:
                    self._run(["connect", addr], timeout=8)
                except AdbError:
                    pass
            out = self._run(["devices"])
            devices = [l.split()[0] for l in out.splitlines()[1:] if l.strip().endswith("device")]
        if not devices:
            raise AdbError("연결된 LDPlayer를 찾지 못했습니다. LDPlayer가 켜져 있는지, "
                           "LDPlayer 설정 > 기타 설정 > ADB 디버깅이 '로컬 연결 허용'인지 확인해주세요.")
        if saved and saved in devices:
            self.serial = saved
        else:
            self.serial = devices[0]
        return self.serial, devices

    def screencap(self):
        """화면을 BGR 이미지(numpy)로 반환"""
        data = self._run(["exec-out", "screencap"], binary=True)
        img = None
        if len(data) >= 12:
            w, h, _fmt = struct.unpack("<III", data[:12])
            if 0 < w < 10000 and 0 < h < 10000:
                header = len(data) - w * h * 4
                if header in (12, 16):
                    arr = np.frombuffer(data, np.uint8, offset=header).reshape(h, w, 4)
                    img = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        if img is None:  # 예비 방식 (PNG)
            data = self._run(["exec-out", "screencap", "-p"], binary=True)
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                raise AdbError("화면 캡처에 실패했습니다.")
        self.size = (img.shape[1], img.shape[0])
        return img

    def tap(self, x, y):
        self._run(["shell", "input", "tap", str(int(x)), str(int(y))], timeout=10)
