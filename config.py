"""
Конфігурація готельного реферального бота.
Відредагуй цей файл перед запуском.
"""

# --- ТОКЕНИ БОТІВ ---
# Отримай у @BotFather в Telegram
GUEST_BOT_TOKEN = "8889548277:AAGUzA11SmLGFqWXQHdlJrCaY8YhNuWgNW0"
ADMIN_BOT_TOKEN = "8810457016:AAEZKc1nlwZ9uX3VCFaEJ1OSu1M8VKbVVL4"

# --- БАЗА ДАНИХ ---
# Отримай у Supabase: Settings -> Database -> Connection string -> URI
DATABASE_URL = "postgresql://postgres.cfcnficftxklpedgrdlg:AntoRost230282!@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"

# --- АДМІНИ ---
# Telegram ID адмінів (можна дізнатись у @userinfobot)
ADMIN_IDS = [
    5450230603,  # Головний адмін
    # 987654321,  # Ресепціоніст 1
    # 111222333,  # Ресепціоніст 2
]

# --- НАЛАШТУВАННЯ ПРОГРАМИ ---
WELCOME_BONUS = 100          # Балів при реєстрації
REFERRAL_BONUS = 100         # Балів рефереру при першій витраті запрошеного
MIN_REDEEM = 100             # Мінімум балів для виведення
POINTS_EXPIRY_MONTHS = 12   # Місяців до анулювання балів без руху

# --- КЕШБЕК (у відсотках) ---
CASHBACK_OWN = 10     # % від власних витрат
CASHBACK_L1 = 5       # % від витрат реферала 1-го рівня
CASHBACK_L2 = 2       # % від витрат реферала 2-го рівня
