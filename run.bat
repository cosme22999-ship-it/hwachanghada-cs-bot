@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ======================================================
echo  화창하다 CS봇 시작
echo ======================================================
echo.

REM 가상환경 확인 및 생성
if not exist ".venv\" (
    echo [*] 가상환경 생성 중... (최초 1회만)
    python -m venv .venv
    if errorlevel 1 (
        echo [!] python 명령이 안 됩니다. Python 3.8+ 설치 후 다시 실행하세요.
        pause
        exit /b 1
    )
)

call .venv\Scripts\activate.bat

REM 의존성 설치 확인 (FastAPI 임포트 가능한지)
python -c "import fastapi" 2>nul
if errorlevel 1 (
    echo [*] 패키지 설치 중... (최초 1회만, 약 3-5분)
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [!] 패키지 설치 실패
        pause
        exit /b 1
    )
)

echo.
echo [*] 서버 시작 - 브라우저에서 http://127.0.0.1:8765 접속
echo [*] 종료: Ctrl+C
echo.

REM 브라우저 자동 오픈 (5초 후)
start /b cmd /c "timeout /t 5 /nobreak >nul && start http://127.0.0.1:8765"

python server.py

pause
