import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Telegram Bot Token
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Google Sheets Configuration
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "service_account.json")
GOOGLE_WORKSHEET_NAME = os.getenv("GOOGLE_WORKSHEET_NAME", "Sheet1")

# OpenAI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Default Image URL (if image_url is empty in sheets2)
DEFAULT_IMAGE_URL = os.getenv("DEFAULT_IMAGE_URL", "")

# Admin Configuration
# Asosiy adminlar - barcha huquqlar
ADMINS = [6964589225,8007366646]
    # Bu yerda asosiy admin ID larni qo'shing
    # Masalan: 123456789, 987654321


# Yordamchi adminlar - cheklangan huquqlar
HELPER_ADMINS = [7947963208]
    # Bu yerda yordamchi admin ID larni qo'shing
    # Masalan: 111222333


# Help phone number (can be changed in admin settings)
HELP_PHONE = "97-310-31-11 raqamiga murojaat qiling."
