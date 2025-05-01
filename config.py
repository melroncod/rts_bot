from dotenv import load_dotenv
import os

load_dotenv()

TOKEN = os.getenv("TOKEN")
ADMIN = os.getenv("ADMIN")  # Ваш Telegram username (или numeric id)