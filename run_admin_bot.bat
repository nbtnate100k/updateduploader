@echo off
echo Installing dependencies...
pip install -r bot_requirements.txt
echo.
echo Starting PLUXO Admin Balance Bot...
python admin_balance_bot.py
pause
