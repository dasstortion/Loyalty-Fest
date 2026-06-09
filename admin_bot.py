"""
Адмін-бот - для персоналу готелю.
"""

import logging
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes
)
from config import ADMIN_BOT_TOKEN, ADMIN_IDS
from database import (
    get_guest_by_phone, process_spend, get_pending_requests,
    approve_redeem, reject_redeem, get_all_guests, init_db
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# --- ПЕРЕВІРКА АДМІНА ---

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS:
            await update.message.reply_text("⛔ Доступ заборонено.")
            return
        return await func(update, context)
    return wrapper


# --- /start ---

@admin_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏨 *Адмін-панель готелю*\n\n"
        "Доступні команди:\n\n"
        "/addspend `<телефон> <сума>` - нарахувати витрати гостю\n"
        "/requests - заявки на виведення балів\n"
        "/approve `<id>` - підтвердити заявку\n"
        "/reject `<id>` - відхилити заявку\n"
        "/users - список гостей\n"
        "/guest `<телефон>` - інфо про гостя",
        parse_mode="Markdown"
    )


# --- /addspend ---

@admin_only
async def addspend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Нарахувати витрати гостю.
    Використання: /addspend +380671234567 5000
    """
    if len(context.args) != 2:
        await update.message.reply_text(
            "Використання: `/addspend <телефон> <сума>`\n"
            "Приклад: `/addspend +380671234567 5000`",
            parse_mode="Markdown"
        )
        return

    phone = context.args[0]
    try:
        amount = int(context.args[1])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Сума має бути цілим позитивним числом.")
        return

    guest = await get_guest_by_phone(phone)
    if not guest:
        await update.message.reply_text(
            f"Гостя з номером `{phone}` не знайдено.\n"
            f"Переконайтесь що гість зареєстрований у боті.",
            parse_mode="Markdown"
        )
        return

    result = await process_spend(guest["id"], amount, update.effective_user.id)
    if not result:
        await update.message.reply_text("Помилка при нарахуванні. Спробуйте ще раз.")
        return

    # Формуємо звіт
    lines = [
        f"✅ *Витрати нараховано*\n",
        f"Гість: {guest['full_name']}",
        f"Телефон: {phone}",
        f"Сума витрат: {amount} грн\n",
        f"*Нараховано балів:*",
        f"- Гостю (10%): +{result['own']} балів",
    ]

    if result["l1"] or result["referral_bonus"]:
        lines.append(f"- Рефереру 1-го рівня (5%): +{result['l1']} балів")
        if result["referral_bonus"]:
            lines.append(f"- Бонус рефереру за першу витрату: +{result['referral_bonus']} балів")

    if result["l2"]:
        lines.append(f"- Рефереру 2-го рівня (2%): +{result['l2']} балів")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    # Повідомляємо гостя
    try:
        await context.bot.send_message(
            chat_id=guest["id"],
            text=f"🎉 Вам нараховано *{result['own']} балів* (кешбек 10% від {amount} грн)!\n\n"
                 f"Поточний баланс перевіряйте командою /balance",
            parse_mode="Markdown"
        )
    except Exception:
        pass  # Гість міг заблокувати бота


# --- /requests ---

@admin_only
async def requests_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показати всі заявки на виведення балів."""
    reqs = await get_pending_requests()

    if not reqs:
        await update.message.reply_text("Немає активних заявок на виведення.")
        return

    text = "📋 *Заявки на виведення балів:*\n\n"
    for r in reqs:
        username = f"@{r['username']}" if r["username"] else "немає username"
        text += (
            f"*Заявка #{r['id']}*\n"
            f"Гість: {r['full_name']} ({username})\n"
            f"Телефон: {r['phone']}\n"
            f"Запитує: {r['amount']} балів ({r['amount']} грн знижки)\n"
            f"Поточний баланс: {r['balance']} балів\n"
            f"Дата: {r['created_at'].strftime('%d.%m.%Y %H:%M')}\n\n"
            f"/approve {r['id']} - підтвердити\n"
            f"/reject {r['id']} - відхилити\n"
            f"{'--' * 15}\n"
        )

    await update.message.reply_text(text, parse_mode="Markdown")


# --- /approve ---

@admin_only
async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Використання: `/approve <id>`", parse_mode="Markdown")
        return

    try:
        request_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID має бути числом.")
        return

    result = await approve_redeem(request_id, update.effective_user.id)

    if result is None:
        await update.message.reply_text(f"Заявку #{request_id} не знайдено або вона вже оброблена.")
        return

    if result == "insufficient":
        await update.message.reply_text("Недостатньо балів на балансі гостя.")
        return

    await update.message.reply_text(
        f"✅ Заявку #{request_id} підтверджено.\n"
        f"Списано *{result['amount']} балів* ({result['amount']} грн знижки).",
        parse_mode="Markdown"
    )

    # Повідомляємо гостя
    try:
        await context.bot.send_message(
            chat_id=result["guest_id"],
            text=f"✅ Вашу заявку на виведення *{result['amount']} балів* підтверджено!\n\n"
                 f"Знижка {result['amount']} грн застосована при заселенні.",
            parse_mode="Markdown"
        )
    except Exception:
        pass


# --- /reject ---

@admin_only
async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Використання: `/reject <id>`", parse_mode="Markdown")
        return

    try:
        request_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID має бути числом.")
        return

    result = await reject_redeem(request_id, update.effective_user.id)

    if not result:
        await update.message.reply_text(f"Заявку #{request_id} не знайдено або вона вже оброблена.")
        return

    await update.message.reply_text(f"❌ Заявку #{request_id} відхилено.")

    # Повідомляємо гостя
    try:
        await context.bot.send_message(
            chat_id=result["guest_id"],
            text=f"❌ Вашу заявку на виведення *{result['amount']} балів* відхилено.\n\n"
                 f"Зверніться на ресепцію для уточнення деталей.",
            parse_mode="Markdown"
        )
    except Exception:
        pass


# --- /users ---

@admin_only
async def users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список всіх гостей з балансами."""
    all_guests = await get_all_guests()

    if not all_guests:
        await update.message.reply_text("Жодного зареєстрованого гостя.")
        return

    text = f"👥 *Гості ({len(all_guests)}):*\n\n"
    for g in all_guests[:20]:  # Перші 20 щоб не перевантажувати
        username = f"@{g['username']}" if g["username"] else "-"
        phone = g["phone"] or "не вказано"
        text += f"• {g['full_name']} | {phone} | {g['balance']} балів\n"

    if len(all_guests) > 20:
        text += f"\n_...та ще {len(all_guests) - 20} гостей._"

    await update.message.reply_text(text, parse_mode="Markdown")


# --- /guest ---

@admin_only
async def guest_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Інформація про конкретного гостя."""
    if not context.args:
        await update.message.reply_text(
            "Використання: `/guest <телефон>`\n"
            "Приклад: `/guest +380671234567`",
            parse_mode="Markdown"
        )
        return

    phone = context.args[0]
    guest = await get_guest_by_phone(phone)

    if not guest:
        await update.message.reply_text(f"Гостя з номером `{phone}` не знайдено.", parse_mode="Markdown")
        return

    from database import get_last_transactions
    transactions = await get_last_transactions(guest["id"], limit=10)

    username = f"@{guest['username']}" if guest["username"] else "немає"
    referrer = f"ID {guest['referrer_id']}" if guest["referrer_id"] else "прямий гість"
    reg_date = guest["registered_at"].strftime("%d.%m.%Y")

    text = (
        f"👤 *{guest['full_name']}*\n"
        f"Телефон: {phone}\n"
        f"Username: {username}\n"
        f"Баланс: *{guest['balance']} балів*\n"
        f"Запросив: {referrer}\n"
        f"Зареєстрований: {reg_date}\n\n"
        f"*Останні транзакції:*\n"
    )

    TX_LABELS = {
        "welcome": "Вітальний бонус",
        "referral_bonus": "Бонус за запрошення",
        "cashback_own": "Кешбек власний",
        "cashback_l1": "Кешбек L1",
        "cashback_l2": "Кешбек L2",
        "redeem": "Виведення",
        "expired": "Анулювання",
    }

    for tx in transactions:
        sign = "+" if tx["amount"] > 0 else ""
        label = TX_LABELS.get(tx["type"], tx["type"])
        date = tx["created_at"].strftime("%d.%m")
        text += f"{date} | {label}: {sign}{tx['amount']}\n"

    await update.message.reply_text(text, parse_mode="Markdown")


# --- ЗАПУСК ---

async def post_init(application):
    await init_db()
    logger.info("База даних ініціалізована.")


def main():
    app = Application.builder().token(ADMIN_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addspend", addspend))
    app.add_handler(CommandHandler("requests", requests_list))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("reject", reject))
    app.add_handler(CommandHandler("users", users))
    app.add_handler(CommandHandler("guest", guest_info))

    logger.info("Адмін-бот запущено.")
    app.run_polling()


if __name__ == "__main__":
    main()
