"""[신고·건의] 디스코드로 보내기 (파이썬 기본 기능만 사용)

받는 주소(웹훅)는 프로그램 안이 아니라 GitHub 저장소의 config.json 에 있음
-> 주소를 바꿔도 프로그램 업데이트 없이 바로 반영됨
"""
import base64
import io
import json
import time
import urllib.request
import uuid

UA_FMT = "DiscordBot (https://github.com/{repo}, {ver})"
MAX_CONTENT = 1900          # 디스코드 메시지 한 개 글자 제한(2000)보다 조금 작게


def _get(url, accept=None, timeout=8):
    headers = {"User-Agent": "LeagueFullPlay-report"}
    if accept:
        headers["Accept"] = accept
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        return r.read()


def webhook_url(repo):
    """저장소 config.json 의 웹훅 주소 (API로 먼저, 안 되면 raw 주소로)"""
    try:
        data = _get(f"https://api.github.com/repos/{repo}/contents/config.json?ref=main",
                    accept="application/vnd.github.raw+json")
    except Exception:
        data = _get(f"https://raw.githubusercontent.com/{repo}/main/config.json?t={int(time.time())}")
    b64 = json.loads(data.decode("utf-8")).get("discord_webhook_b64", "")
    url = base64.b64decode(b64).decode("utf-8").strip()
    if not url.startswith("https://discord.com/api/webhooks/"):
        raise RuntimeError("제보 받는 주소가 설정되어 있지 않아요")
    return url


def _multipart(fields, files):
    """fields: {이름: 문자열}, files: [(필드이름, 파일이름, 바이트, 종류)]"""
    boundary = "----lfp" + uuid.uuid4().hex
    buf = io.BytesIO()
    for name, value in fields.items():
        buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n"
                  f"Content-Type: application/json\r\n\r\n".encode())
        buf.write(value.encode("utf-8"))
        buf.write(b"\r\n")
    for field, filename, data, ctype in files:
        buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
                  f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n".encode())
        buf.write(data)
        buf.write(b"\r\n")
    buf.write(f"--{boundary}--\r\n".encode())
    return buf.getvalue(), f"multipart/form-data; boundary={boundary}"


def build_message(kind, name, text, info_lines):
    """디스코드에 올라갈 글"""
    title = "🛠️ 고장 신고" if kind == "bug" else "💡 건의"
    who = name.strip() or "(이름 없음)"
    lines = [f"**{title}** — {who}"]
    lines += info_lines
    lines += ["", text.strip() or "(내용 없음)"]
    msg = "\n".join(lines)
    if len(msg) > MAX_CONTENT:
        msg = msg[:MAX_CONTENT - 20] + "\n…(길어서 잘림)"
    return msg


def send(repo, version, kind, name, text, info_lines=(), log_text=None, screen_png=None,
         url=None, timeout=20):
    """제보 보내기. 실패하면 예외"""
    url = url or webhook_url(repo)
    payload = {
        "username": "리그 풀플레이 제보",
        "content": build_message(kind, name, text, list(info_lines)),
        "allowed_mentions": {"parse": []},          # @everyone 같은 호출 막기
    }
    files = []
    if log_text:
        files.append(("files[0]", "log.txt", log_text.encode("utf-8"), "text/plain"))
    if screen_png:
        files.append((f"files[{len(files)}]", "screen.jpg", screen_png, "image/jpeg"))
    body, ctype = _multipart({"payload_json": json.dumps(payload, ensure_ascii=False)}, files)
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": ctype,
        "User-Agent": UA_FMT.format(repo=repo, ver=version),
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status not in (200, 204):
            raise RuntimeError(f"디스코드 응답 {r.status}")
    return True
