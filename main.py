"""
Запуск обох ботів одночасно.
"""

import asyncio
import logging
from telegram.ext import Application
from config import GUEST_BOT_TOKEN, ADMIN_BOT_TOKEN
from database import init_db
from scheduler import daily_expire_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    await init_db()
    logger.info("База даних готова.")

    # Імпортуємо головні функції з кожного бота
    import guest_bot
    import admin_bot

    guest_app = Application.builder().token(GUEST_BOT_TOKEN).build()
    admin_app = Application.builder().token(ADMIN_BOT_TOKEN).build()

    # Реєструємо хендлери
    from telegram.ext import CommandHandler, MessageHandler, filters, ConversationHandler, CallbackQueryHandler
    from guest_bot import (
        start, receive_phone, balance, invite,
        redeem_start, redeem_amount, cancel, menu_handler,
        WAITING_PHONE, WAITING_REDEEM_AMOUNT
    )
    from admin_bot import (
        start as admin_start, addspend, requests_list,
        users, guest_info, button_handler
    )

    # Гостьовий бот
    reg_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={WAITING_PHONE: [MessageHandler(filters.CONTACT, receive_phone)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    redeem_conv = ConversationHandler(
        entry_points=[
            CommandHandler("redeem", redeem_start),
            MessageHandler(filters.Regex("^💸 Вивести бали$"), redeem_start),
        ],
        states={WAITING_REDEEM_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, redeem_amount)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    guest_app.add_handler(reg_conv)
    guest_app.add_handler(redeem_conv)
    guest_app.add_handler(CommandHandler("balance", balance))
    guest_app.add_handler(CommandHandler("invite", invite))
    guest_app.add_handler(MessageHandler(filters.Regex("^(💰 Мій баланс|🔗 Запросити друга)$"), menu_handler))

    # Адмін-бот
    admin_app.add_handler(CommandHandler("start", admin_start))
    admin_app.add_handler(CommandHandler("addspend", addspend))
    admin_app.add_handler(CommandHandler("requests", requests_list))
    admin_app.add_handler(CommandHandler("users", users))
    admin_app.add_handler(CommandHandler("guest", guest_info))
    admin_app.add_handler(CallbackQueryHandler(button_handler))

    # Запускаємо все паралельно
    logger.info("Запуск обох ботів...")
    async with guest_app, admin_app:
        await guest_app.start()
        await admin_app.start()
        await guest_app.updater.start_polling()
        await admin_app.updater.start_polling()

        # Запускаємо планувальник
        asyncio.create_task(daily_expire_job())

        logger.info("Обидва боти працюють. Натисніть Ctrl+C для зупинки.")
        await asyncio.Event().wait()  # Чекаємо нескінченно

        await guest_app.updater.stop()
        await admin_app.updater.stop()
        await guest_app.stop()
        await admin_app.stop()


if __name__ == "__main__":
    asyncio.run(main())
