# 리그 풀플레이 (LeagueFullPlay)

컴투스프로야구V26 리그모드를 직접 플레이로 자동 진행하는 PC 프로그램입니다. (LDPlayer 세로 9:16)

- 투구 화면에서 [포수 리드], 투수 체력 저하 팝업에서 [교체]
- 미션 게이지 135를 채우면 시뮬레이션으로 전환
- 경기가 끝나면 [다음] → [다음] → [한 번 더 하기] 반복, 빈 곳을 눌러 컷씬 넘기기

## 받기
**[전체 받기 (zip)](https://github.com/wnsgur2791-hy/LeagueFullPlay/archive/refs/heads/main.zip)** — 또는 이 페이지 위의 초록색 [Code] → [Download ZIP]

1. zip을 풀면 나오는 `LeagueFullPlay` 폴더만 원하는 곳으로 옮기고, 그 안의 **사용법.txt** 를 따라 설치합니다.
2. `run.bat` 으로 켭니다.

## 자동 업데이트
`run.bat` 으로 켤 때마다 이 저장소의 `latest.json`을 확인해서, 새 버전이면 바뀐 파일만 받아 자동으로 바꿉니다.
내 설정(config.json), 로그, 저장한 화면은 그대로 남습니다.

## 폴더
- `LeagueFullPlay/` 프로그램 원본
- `latest.json` 최신 버전 번호와 파일 목록 (자동 업데이트가 읽음)
- `tools/release.py` 새 버전 배포하기
