from dotenv import load_dotenv
import os

load_dotenv()

TOKEN = os.getenv("TOKEN")
ADMIN = os.getenv("ADMIN")
FASTAPI_URL = os.getenv("FASTAPI_URL", "http://127.0.0.1:8000")