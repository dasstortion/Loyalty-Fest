"""
Гостьовий бот - для гостей готелю.
"""

import logging
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
from config import GUEST_BOT_TOKEN, WELCOME_BONUS, MIN_REDEEM
from database import (
    get_guest, register_guest, add_points, get_balance,
    get_last_transactions, create_redeem_request, init_db, get_conn
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Стани ConversationHandler
WAITING_PHONE = 1
WAITING_REDEEM_AMOUNT = 2

TX_LABELS = {
    "welcome":        "Вітальний бонус",
    "referral_bonus": "Бонус за запрошення",
    "cashback_own":   "Кешбек від витрат",
    "cashback_l1":    "Кешбек від реферала",
    "cashback_l2":    "Кешбек від реферала 2-го рівня",
    "redeem":         "Виведення балів",
    "expired":        "Анулювання балів",
}


# --- /start ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    guest = await get_guest(user.id)

    # Перевіряємо реферальний параметр
    referrer_id = None
    if context.args:
        try:
            referrer_id = int(context.args[0])
            if referrer_id == user.id:
                referrer_id = None
        except ValueError:
            pass

    if guest and guest["phone"]:
        await update.message.reply_text(
            f"З поверненням, {user.first_name}! 👋\n\n"
            f"Ваш поточний баланс: *{guest['balance']} балів*\n\n"
            "Скористайтесь меню нижче:",
            parse_mode="Markdown",
            reply_markup=main_menu()
        )
        return ConversationHandler.END

    # Новий гість - запитуємо телефон
    context.user_data["referrer_id"] = referrer_id

    button = KeyboardButton("📱 Поділитись номером телефону", request_contact=True)
    markup = ReplyKeyboardMarkup([[button]], resize_keyboard=True, one_time_keyboard=True)

    await update.message.reply_text(
        f"Вітаємо в програмі лояльності готелю! 🏨\n\n"
        f"Щоб зареєструватись та отримати *{WELCOME_BONUS} вітальних балів*, "
        f"натисніть кнопку нижче для підтвердження номера телефону.",
        parse_mode="Markdown",
        reply_markup=markup
    )
    return WAITING_PHONE


async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    contact = update.message.contact

    if contact.user_id != user.id:
        await update.message.reply_text("Будь ласка, надішліть свій власний номер телефону.")
        return WAITING_PHONE

    referrer_id = context.user_data.get("referrer_id")

    # Перевіряємо чи цей телефон вже зареєстрований
    from database import get_guest_by_phone
    existing = await get_guest_by_phone(contact.phone_number)
    if existing and existing["id"] != user.id:
        await update.message.reply_text(
            "Цей номер телефону вже зареєстрований в системі. "
            "Зверніться на ресепцію готелю.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    # Реєструємо гостя
    await register_guest(
        guest_id=user.id,
        full_name=user.full_name,
        username=user.username,
        phone=contact.phone_number,
        referrer_id=referrer_id
    )

    # Нараховуємо вітальний бонус
    conn = await get_conn()
    try:
        await add_points(conn, user.id, WELCOME_BONUS, "welcome", "Вітальний бонус при реєстрації")
    finally:
        await conn.close()

    referrer_text = ""
    if referrer_id:
        referrer_text = "\nВас запросив друг - разом ви збираєте більше балів! 🤝"

    await update.message.reply_text(
        f"✅ Реєстрацію завершено!\n\n"
        f"Ваш номер телефону підтверджено.\n"
        f"На ваш рахунок нараховано *{WELCOME_BONUS} вітальних балів*!{referrer_text}\n\n"
        f"Користуйтесь меню нижче:",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )
    return ConversationHandler.END


# --- /balance ---

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    guest = await get_guest(user.id)

    if not guest or not guest["phone"]:
        await update.message.reply_text("Спочатку зареєструйтесь через /start")
        return

    transactions = await get_last_transactions(user.id, limit=5)

    tx_text = ""
    if transactions:
        tx_text = "\n\n*Останні операції:*\n"
        for tx in transactions:
            sign = "+" if tx["amount"] > 0 else ""
            label = TX_LABELS.get(tx["type"], tx["type"])
            date = tx["created_at"].strftime("%d.%m.%Y")
            tx_text += f"{date} | {label}: {sign}{tx['amount']} балів\n"

    await update.message.reply_text(
        f"💰 *Ваш баланс: {guest['balance']} балів*\n"
        f"_(1 бал = 1 гривня знижки)_{tx_text}",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )


# --- /invite ---

async def invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    guest = await get_guest(user.id)

    if not guest or not guest["phone"]:
        await update.message.reply_text("Спочатку зареєструйтесь через /start")
        return

    bot = context.bot
    link = f"https://t.me/{bot.username}?start={user.id}"

    await update.message.reply_text(
        f"🔗 *Ваше реферальне посилання:*\n\n"
        f"`{link}`\n\n"
        f"Поділіться з друзями чи знайомими!\n\n"
        f"*Як це працює:*\n"
        f"- Друг реєструється за вашим посиланням - отримує 100 балів\n"
        f"- Після першої оплати друга - ви отримуєте 100 балів\n"
        f"- З кожної оплати друга - ви отримуєте 5% балами\n"
        f"- З оплат їхніх друзів - ще 2% балами",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )


# --- /redeem ---

async def redeem_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    guest = await get_guest(user.id)

    if not guest or not guest["phone"]:
        await update.message.reply_text("Спочатку зареєструйтесь через /start")
        return ConversationHandler.END

    if guest["balance"] < MIN_REDEEM:
        await update.message.reply_text(
            f"На жаль, для виведення потрібно мінімум *{MIN_REDEEM} балів*.\n"
            f"Ваш поточний баланс: *{guest['balance']} балів*.",
            parse_mode="Markdown",
            reply_markup=main_menu()
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"💸 *Виведення балів*\n\n"
        f"Ваш баланс: *{guest['balance']} балів*\n"
        f"Мінімум для виведення: {MIN_REDEEM} балів\n\n"
        f"Введіть кількість балів для виведення\n"
        f"_(знижка застосовується на ресепції при заселенні)_",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return WAITING_REDEEM_AMOUNT


async def redeem_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    guest = await get_guest(user.id)

    try:
        amount = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Введіть ціле число, наприклад: 200")
        return WAITING_REDEEM_AMOUNT

    if amount < MIN_REDEEM:
        await update.message.reply_text(f"Мінімальна сума виведення - {MIN_REDEEM} балів.")
        return WAITING_REDEEM_AMOUNT

    if amount > guest["balance"]:
        await update.message.reply_text(
            f"Недостатньо балів. Ваш баланс: {guest['balance']} балів."
        )
        return WAITING_REDEEM_AMOUNT

    request_id = await create_redeem_request(user.id, amount)

    await update.message.reply_text(
        f"✅ Заявку #{request_id} на виведення *{amount} балів* ({amount} грн знижки) подано!\n\n"
        f"Адміністратор підтвердить заявку при заселенні. "
        f"Ви отримаєте повідомлення після підтвердження.",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скасовано.", reply_markup=main_menu())
    return ConversationHandler.END


# --- МЕНЮ ---

def main_menu():
    return ReplyKeyboardMarkup(
        [
            ["💰 Мій баланс", "🔗 Запросити друга"],
            ["💸 Вивести бали"],
        ],
        resize_keyboard=True
    )


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "💰 Мій баланс":
        await balance(update, context)
    elif text == "🔗 Запросити друга":
        await invite(update, context)
    elif text == "💸 Вивести бали":
        return await redeem_start(update, context)


# --- ЗАПУСК ---

async def post_init(application):
    await init_db()
    logger.info("База даних ініціалізована.")


def main():
    app = Application.builder().token(GUEST_BOT_TOKEN).post_init(post_init).build()

    # ConversationHandler для реєстрації
    reg_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAITING_PHONE: [MessageHandler(filters.CONTACT, receive_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # ConversationHandler для виведення балів
    redeem_conv = ConversationHandler(
        entry_points=[
            CommandHandler("redeem", redeem_start),
            MessageHandler(filters.Regex("^💸 Вивести бали$"), redeem_start),
        ],
        states={
            WAITING_REDEEM_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, redeem_amount)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(reg_conv)
    app.add_handler(redeem_conv)
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("invite", invite))
    app.add_handler(MessageHandler(
        filters.Regex("^(💰 Мій баланс|🔗 Запросити друга)$"), menu_handler
    ))

    logger.info("Гостьовий бот запущено.")
    app.run_polling()


if __name__ == "__main__":
    main()
