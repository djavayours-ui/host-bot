"""Sozlamalar (muhit o'zgaruvchilaridan)."""
import os
import re

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("MASTER_BOT_TOKEN") or ""
# Asosiy adminlar (egasi): ID'lar vergul bilan. Ular barcha yaratilgan botlarda ham YASHIRIN admin.
ADMIN_IDS = {int(x) for x in re.findall(r"\d+", os.getenv("ADMIN_IDS") or os.getenv("ADMIN_USERS")
                                       or os.getenv("OWNER_ID") or "")}
DATA_DIR = os.getenv("DATA_DIR", "data")
TZ_NAME = os.getenv("TZ_NAME", "Asia/Tashkent")
BRAND = os.getenv("BRAND", "Avenor Builder")

MASTER_USERNAME = ""   # ishga tushganda main.py to'ldiradi (deep-link uchun)
