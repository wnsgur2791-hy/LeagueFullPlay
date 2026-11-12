"""자동 업데이트 (파이썬 기본 기능만 사용 - 다른 부품이 없어도 돌아가게)

동작 순서 (컴프야 매크로.bat -> launcher.py 가 프로그램을 켜기 직전에 부름)
1. GitHub 저장소의 latest.json(최신 버전 번호 + 파일 목록)을 읽음
2. 내 버전보다 새 버전이면, 내 파일과 다른 파일만 골라서 받음
3. 지금 app 폴더를 '업데이트백업' 폴더에 복사해 둔 뒤 새 파일로 바꿈
   - config.json(내 설정), logs, captures 는 건드리지 않음 (받는 목록에 들어 있지도 않음)
   - .bat 파일은 실행 중에 바꾸면 윈도우가 이상하게 동작해서 바꾸지 않음
4. 새 버전 화면이 뜨고 3초가 지나면 '정상 실행' 표시를 남김.
   새 버전이 켜지다 죽으면, 다음에 켤 때 백업으로 되돌림 (같은 버전이 두 번 실패하면 그 버전은 건너뜀)
"""
import datetime
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)
BACKUP_DIR = os.path.join(ROOT_DIR, "업데이트백업")
MARKER = os.path.join(BACKUP_DIR, "확인대기.json")
SKIP = os.path.join(BACKUP_DIR, "건너뛸버전.json")
HISTORY = os.path.join(BACKUP_DIR, "업데이트기록.json")
ROOT_FILES = ("사용법.txt", "requirements.txt")     # 앱 폴더 밖에서 바꿔도 되는 파일
UA = "LeagueFullPlay-updater"


def current_version():
    try:
        sys.path.insert(0, APP_DIR)
        import importlib
        import version
        importlib.reload(version)
        return version.VERSION, version.REPO, version.PROG_DIR
    except Exception:
        return "0.0.0", "wnsgur2791-hy/LeagueFullPlay", "LeagueFullPlay"


def vtuple(v):
    v = str(v or "").strip().lstrip("vV")
    out = []
    for p in v.split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple((out + [0, 0, 0])[:3])


def _load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def enabled():
    cfg = _load(os.path.join(ROOT_DIR, "config.json"), {}) or {}
    return cfg.get("auto_update", True) is not False


def http_get(url, timeout=8, accept=None):
    headers = {"User-Agent": UA}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _fetch_latest_json(repo, timeout):
    """latest.json 읽기.
    1순위 GitHub API: 거의 바로 최신 내용이 보임 (raw 주소는 GitHub가 최대 5분 동안 예전 내용을 줌)
    2순위 raw 주소: API가 막혔을 때 (시간당 횟수 제한 등)"""
    try:
        url = f"https://api.github.com/repos/{repo}/contents/latest.json?ref=main"
        return json.loads(http_get(url, timeout, accept="application/vnd.github.raw+json").decode("utf-8"))
    except Exception:
        url = f"https://raw.githubusercontent.com/{repo}/main/latest.json?t={int(time.time())}"
        return json.loads(http_get(url, timeout).decode("utf-8"))


def latest_release(repo, prog_dir, timeout=6):
    """저장소 main 의 latest.json -> {'version', 'notes', 'ref', 'files': {경로: sha256}}"""
    data = _fetch_latest_json(repo, timeout)
    notes = data.get("notes") or ""
    if isinstance(notes, list):
        notes = "\n".join(f"- {n}" for n in notes)
    return {"version": str(data.get("version") or "").lstrip("vV"), "notes": notes,
            "ref": data.get("ref") or "main", "files": data.get("files") or {},
            "repo": repo, "prog_dir": data.get("prog_dir") or prog_dir}


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_name(name):
    name = name.replace("\\", "/")
    if name.startswith("/") or ".." in name.split("/") or ":" in name:
        return None
    return name


def _wanted(rel):
    top = rel.split("/")[0]
    return (top == "app" or rel in ROOT_FILES) and "__pycache__" not in rel and not rel.endswith(".pyc")


def download_changed(rel, log=print):
    """내 PC 파일과 다른 파일만 받아서 zip(바이트)으로 묶음 -> apply_zip 에 넘김"""
    buf = io.BytesIO()
    n = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, sha in sorted(rel["files"].items()):
            path = _safe_name(path)
            if not path or not _wanted(path):
                continue
            local = os.path.join(ROOT_DIR, *path.split("/"))
            if os.path.isfile(local) and _sha(local) == sha:
                continue                                   # 같은 파일은 안 받음
            url = (f"https://raw.githubusercontent.com/{rel['repo']}/{rel['ref']}/"
                   + urllib.parse.quote(f"{rel['prog_dir']}/{path}"))
            data = http_get(url, timeout=30)
            if hashlib.sha256(data).hexdigest() != sha:
                raise RuntimeError(f"{path} 파일이 제대로 받아지지 않았어요")
            z.writestr(path, data)
            n += 1
    log(f"  바뀐 파일 {n}개를 받았어요.")
    return buf.getvalue(), n


def check(log=print):
    """새 버전이 있으면 정보, 없으면 None (받거나 바꾸지는 않음)"""
    cur, repo, prog_dir = current_version()
    rel = latest_release(repo, prog_dir)
    if not rel or not rel["version"] or vtuple(rel["version"]) <= vtuple(cur):
        return None
    if (_load(SKIP, {}) or {}).get(rel["version"], 0) >= 2:
        log(f"  (v{rel['version']}은 이 PC에서 두 번 켜지지 않아서 건너뛰어요)")
        return None
    return rel


# ------------------------------------------------------------ 적용
def _backup(cur):
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"v{cur}_{stamp}")
    k = 1
    while os.path.exists(dst):
        dst = os.path.join(BACKUP_DIR, f"v{cur}_{stamp}_{k}")
        k += 1
    shutil.copytree(APP_DIR, os.path.join(dst, "app"), ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for fn in ROOT_FILES:
        p = os.path.join(ROOT_DIR, fn)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(dst, fn))
    # 백업은 최근 2개만 남김
    olds = sorted(d for d in os.listdir(BACKUP_DIR)
                  if d.startswith("v") and os.path.isdir(os.path.join(BACKUP_DIR, d)))
    for d in olds[:-2]:
        shutil.rmtree(os.path.join(BACKUP_DIR, d), ignore_errors=True)
    return dst


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".새버전"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _read_text(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def _clear_pycache():
    """파이썬이 미리 읽어 둔 파일(__pycache__)을 지움 -> 다음 실행 때 새 코드를 확실히 읽게"""
    shutil.rmtree(os.path.join(APP_DIR, "__pycache__"), ignore_errors=True)


def apply_zip(raw, new_ver, notes="", log=print):
    """받은 파일을 프로그램 폴더에 반영"""
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = [n for n in zf.namelist() if not n.endswith("/")]
    if zf.testzip() is not None or "app/version.py" not in names:
        raise RuntimeError("받은 업데이트 파일이 깨져 있어요")
    cur = current_version()[0]
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup = _backup(cur)
    old_req = _read_text(os.path.join(ROOT_DIR, "requirements.txt"))
    n_new = 0
    for name in names:
        rel = _safe_name(name)
        if not rel or not _wanted(rel):
            continue
        _write(os.path.join(ROOT_DIR, *rel.split("/")), zf.read(name))
        n_new += 1
    _clear_pycache()
    _save(MARKER, {"from": cur, "to": new_ver, "backup": backup, "notes": notes,
                   "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    hist = _load(HISTORY, []) or []
    hist.append({"time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "from": cur, "to": new_ver,
                 "files": n_new, "notes": notes})
    _save(HISTORY, hist[-30:])
    log(f"  v{cur} → v{new_ver} 업데이트 완료 (파일 {n_new}개). 이전 버전은 업데이트백업 폴더에 있어요.")
    if _read_text(os.path.join(ROOT_DIR, "requirements.txt")) != old_req:
        log("  필요한 부품이 바뀌어서 설치할게요... (1~3분)")
        try:
            subprocess.call([sys.executable, "-m", "pip", "install", "-r",
                             os.path.join(ROOT_DIR, "requirements.txt")], timeout=900)
        except Exception as e:
            log(f"  ⚠ 부품 설치 실패: {e}")
    return True


def _fetch_and_apply(rel, log):
    raw, n = download_changed(rel, log)
    if n == 0:
        # 파일은 이미 같음 (예: 전체를 새로 받은 경우) -> 버전 번호만 맞으면 끝
        return rel["version"]
    apply_zip(raw, rel["version"], rel.get("notes", ""), log)
    return rel["version"]


def auto_update(log=print):
    """켤 때 한 번: 새 버전이 있으면 받아서 바로 바꿈. 바꿨으면 새 버전 번호"""
    if not enabled():
        return None
    try:
        rel = check(log)
    except Exception as e:
        log(f"  (업데이트 확인을 못 했어요: {e.__class__.__name__}. 지금 버전으로 켤게요)")
        return None
    if not rel:
        log("  최신 버전이에요.")
        return None
    if not rel.get("files"):
        log(f"  새 버전 v{rel['version']}이 있지만 파일 목록이 없어요. 지금 버전으로 켤게요.")
        return None
    log(f"  새 버전 v{rel['version']}을 받는 중...")
    try:
        return _fetch_and_apply(rel, log)
    except Exception as e:
        log(f"  ⚠ 업데이트 실패: {e} (지금 버전으로 켤게요)")
        return None


def update_now(log=print):
    """(프로그램이 켜진 상태에서) 새 버전이 있으면 받아서 바꿈. 적용은 다시 켤 때"""
    rel = check(log)
    if not rel or not rel.get("files"):
        return None
    return _fetch_and_apply(rel, log)


# ------------------------------------------------------------ 정상 실행 확인 / 되돌리기
def confirm_ok():
    """새 버전 화면이 정상으로 떴을 때 부름. 방금 업데이트했다면 그 정보를 돌려줌"""
    m = _load(MARKER)
    if m is None:
        return None
    try:
        os.remove(MARKER)
    except OSError:
        pass
    return m


def rollback_if_needed(log=print):
    """'확인대기' 표시가 남아 있으면 = 지난번 새 버전이 정상으로 켜지지 못함 -> 백업으로 되돌림"""
    m = _load(MARKER)
    if not m:
        return False
    backup = m.get("backup")
    if not backup or not os.path.isdir(os.path.join(backup, "app")):
        try:
            os.remove(MARKER)
        except OSError:
            pass
        return False
    log(f"  ⚠ v{m.get('to')}이 정상으로 켜지지 않았던 것 같아서 v{m.get('from')}으로 되돌릴게요.")
    src = os.path.join(backup, "app")
    for base, _dirs, files in os.walk(src):
        rel = os.path.relpath(base, src)
        for fn in files:
            dst = os.path.join(APP_DIR, rel, fn) if rel != "." else os.path.join(APP_DIR, fn)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(base, fn), dst)
    for fn in ROOT_FILES:
        p = os.path.join(backup, fn)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(ROOT_DIR, fn))
    _clear_pycache()
    skip = _load(SKIP, {}) or {}
    skip[m.get("to")] = skip.get(m.get("to"), 0) + 1
    _save(SKIP, skip)
    try:
        os.remove(MARKER)
    except OSError:
        pass
    return True
