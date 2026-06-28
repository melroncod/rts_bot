from dotenv import load_dotenv
import os

load_dotenv()

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError(
        "Не задана переменная окружения TOKEN (токен Telegram-бота). "
        "Скопируйте .env.example в .env и заполните значения."
    )

raw_admins = os.getenv("ADMIN", "")
ADMIN = [int(x) for x in raw_admins.split(",") if x.strip().isdigit()]
if not ADMIN:
    raise RuntimeError(
        "Не задан ни один администратор в переменной ADMIN "
        "(числовые Telegram ID через запятую)."
    )

# Username для кнопки «Поддержка». Храним без ведущего '@'.
ADMIN_USER = (os.getenv("ADMIN_USER") or "").lstrip("@")
FASTAPI_URL = os.getenv("FASTAPI_URL", "http://127.0.0.1:8000")