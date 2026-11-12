@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=python
where py >nul 2>nul
if %errorlevel%==0 set PY=py -3
%PY% --version >nul 2>nul
if errorlevel 1 goto nopython
if exist ".installed" goto run
echo 처음 실행이라 필요한 부품을 설치합니다. 1~2분 정도 걸려요...
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto installfail
echo ok> .installed
:run
%PY% app\launcher.py
if errorlevel 1 pause
exit /b
:nopython
echo [오류] 파이썬이 설치되어 있지 않습니다. 사용법.txt 의 1단계를 봐주세요.
pause
exit /b
:installfail
echo [오류] 부품 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 실행해주세요.
pause
