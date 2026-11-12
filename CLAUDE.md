# 작업 메모 (Claude용)

- 프로그램 원본: `LeagueFullPlay/` (Python + tkinter + OpenCV, Windows 사용자용). LDPlayer + ADB로 컴프야V26 리그모드를 자동 진행.
  사용자 설명은 한국어, 비전공자 눈높이로.
- 구조: `LeagueFullPlay/app/` = 업데이트로 바뀌는 코드·템플릿. `LeagueFullPlay/` 바로 아래 config.json(사용자가 바꾼 설정만), logs, captures, 업데이트백업 은 사용자 것.
  기본 설정은 `app/config_default.json`. 템플릿 좌표는 화면을 768x1366으로 맞춘 기준 (`app/templates/meta.json`).
- 새 버전 배포: 코드 수정 → `python tools/release.py X.Y.Z "바뀐 내용" ...`
  (version.py 수정 + commit/push → 그 커밋 번호와 파일 목록(sha256)을 latest.json 에 적어 commit/push).
- 사용자 PC는 run.bat → `app/launcher.py` → `app/updater.py`가 main 의 latest.json 을 읽고, 다른 파일만
  raw.githubusercontent.com/<repo>/<ref>/LeagueFullPlay/<경로> 에서 받아 적용. .bat 파일은 반영 안 됨.
  새 버전이 켜지다 죽으면 다음 실행 때 업데이트백업에서 되돌림.
- 저장소 루트 `config.json` 의 discord_webhook_b64 = [신고·건의]를 받는 디스코드 웹훅(base64). 바꾸면 즉시 반영 (app/report.py가 GitHub API→raw 순으로 읽음).
- 고급 설정 탭: 템플릿별 일치도 덮어쓰기는 사용자 config.json 의 thresholds. 포수 리드는 meta.json 의 search 영역(아래쪽 절반 전체)에서 찾음.
