from dotenv import load_dotenv
import os

load_dotenv()

TOKEN = os.getenv("TOKEN")
raw_admins = os.getenv("ADMIN", "")
ADMIN = [int(x) for x in raw_admins.split(",") if x.strip()]
FASTAPI_URL = os.getenv("FASTAPI_URL", "http://127.0.0.1:8000")