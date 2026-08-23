import os

# Токен бота из @BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN", "8809902412:AAHtg9naSNY2vJNZ8M_Jy4OXBNwtE6V4j48")

# Telegram ID главного администратора (узнать свой ID: @userinfobot)
ADMIN_ID = int(os.getenv("ADMIN_ID", "548363156"))

# Путь к базе данных
DB_PATH = os.getenv("DB_PATH", "crm.db")

# Настройки HTTP-сервера для приёма вебхуков от Google Таблиц
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "super-secret-key-123")
