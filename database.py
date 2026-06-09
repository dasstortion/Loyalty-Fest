"""
Робота з базою даних PostgreSQL (Supabase).
"""

import asyncpg
from datetime import datetime, timedelta
from config import DATABASE_URL, POINTS_EXPIRY_MONTHS


# --- ПІДКЛЮЧЕННЯ ---

async def get_conn():
    return await asyncpg.connect(DATABASE_URL)


# --- ІНІЦІАЛІЗАЦІЯ СХЕМИ ---

CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS guests (
    id              BIGINT PRIMARY KEY,         -- Telegram user_id
    username        TEXT,                        -- @username (може бути NULL)
    full_name       TEXT NOT NULL,
    phone           TEXT UNIQUE,                 -- номер телефону
    referrer_id     BIGINT REFERENCES guests(id),
    balance         INTEGER NOT NULL DEFAULT 0,
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_activity   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS transactions (
    id              SERIAL PRIMARY KEY,
    guest_id        BIGINT NOT NULL REFERENCES guests(id),
    amount          INTEGER NOT NULL,            -- балів (+ нарахування, - списання)
    type            TEXT NOT NULL,               -- тип транзакції (див. нижче)
    description     TEXT,
    related_guest_id BIGINT REFERENCES guests(id), -- від кого прийшов бонус
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Типи транзакцій:
-- welcome          - бонус при реєстрації
-- referral_bonus   - бонус за першу витрату запрошеного
-- cashback_own     - 10% від власних витрат
-- cashback_l1      - 5% від витрат реферала 1-го рівня
-- cashback_l2      - 2% від витрат реферала 2-го рівня
-- redeem           - списання при виведенні
-- expired          - анулювання балів

CREATE TABLE IF NOT EXISTS redeem_requests (
    id              SERIAL PRIMARY KEY,
    guest_id        BIGINT NOT NULL REFERENCES guests(id),
    amount          INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending', -- pending / approved / rejected
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    resolved_by     BIGINT                        -- Telegram ID адміна
);

CREATE TABLE IF NOT EXISTS spend_log (
    id              SERIAL PRIMARY KEY,
    guest_id        BIGINT NOT NULL REFERENCES guests(id),
    amount          INTEGER NOT NULL,            -- сума витрат у гривнях
    is_first        BOOLEAN NOT NULL DEFAULT FALSE,
    added_by        BIGINT NOT NULL,             -- Telegram ID адміна
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transactions_guest ON transactions(guest_id);
CREATE INDEX IF NOT EXISTS idx_transactions_created ON transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_guests_phone ON guests(phone);
"""


async def init_db():
    conn = await get_conn()
    try:
        await conn.execute(CREATE_SCHEMA)
    finally:
        await conn.close()


# --- ГОСТІ ---

async def get_guest(guest_id: int):
    conn = await get_conn()
    try:
        return await conn.fetchrow(
            "SELECT * FROM guests WHERE id = $1", guest_id
        )
    finally:
        await conn.close()


async def get_guest_by_phone(phone: str):
    conn = await get_conn()
    try:
        # Нормалізуємо: +380... або 380... або 0...
        normalized = normalize_phone(phone)
        return await conn.fetchrow(
            "SELECT * FROM guests WHERE phone = $1", normalized
        )
    finally:
        await conn.close()


async def register_guest(guest_id: int, full_name: str, username: str, phone: str, referrer_id: int = None):
    conn = await get_conn()
    try:
        normalized_phone = normalize_phone(phone)
        await conn.execute(
            """
            INSERT INTO guests (id, full_name, username, phone, referrer_id)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (id) DO UPDATE
              SET full_name = EXCLUDED.full_name,
                  username = EXCLUDED.username,
                  phone = EXCLUDED.phone
            """,
            guest_id, full_name, username, normalized_phone, referrer_id
        )
    finally:
        await conn.close()


async def update_last_activity(guest_id: int):
    conn = await get_conn()
    try:
        await conn.execute(
            "UPDATE guests SET last_activity = NOW() WHERE id = $1", guest_id
        )
    finally:
        await conn.close()


async def get_all_guests():
    conn = await get_conn()
    try:
        return await conn.fetch(
            "SELECT * FROM guests ORDER BY registered_at DESC"
        )
    finally:
        await conn.close()


# --- БАЛАНС І ТРАНЗАКЦІЇ ---

async def add_points(conn, guest_id: int, amount: int, tx_type: str, description: str, related_guest_id: int = None):
    """Нарахувати або списати бали. Використовує існуюче підключення (для транзакцій)."""
    await conn.execute(
        """
        UPDATE guests SET balance = balance + $1, last_activity = NOW()
        WHERE id = $2
        """,
        amount, guest_id
    )
    await conn.execute(
        """
        INSERT INTO transactions (guest_id, amount, type, description, related_guest_id)
        VALUES ($1, $2, $3, $4, $5)
        """,
        guest_id, amount, tx_type, description, related_guest_id
    )


async def get_balance(guest_id: int):
    conn = await get_conn()
    try:
        row = await conn.fetchrow("SELECT balance FROM guests WHERE id = $1", guest_id)
        return row["balance"] if row else 0
    finally:
        await conn.close()


async def get_last_transactions(guest_id: int, limit: int = 5):
    conn = await get_conn()
    try:
        return await conn.fetch(
            """
            SELECT t.*, g.full_name as related_name
            FROM transactions t
            LEFT JOIN guests g ON g.id = t.related_guest_id
            WHERE t.guest_id = $1
            ORDER BY t.created_at DESC
            LIMIT $2
            """,
            guest_id, limit
        )
    finally:
        await conn.close()


# --- НАРАХУВАННЯ ВІД ВИТРАТ ---

async def has_first_spend(guest_id: int) -> bool:
    """Перевірити чи були вже витрати у цього гостя."""
    conn = await get_conn()
    try:
        row = await conn.fetchrow(
            "SELECT id FROM spend_log WHERE guest_id = $1 LIMIT 1", guest_id
        )
        return row is not None
    finally:
        await conn.close()


async def process_spend(guest_id: int, spend_amount: int, admin_id: int):
    """
    Нарахувати кешбек від витрати.
    Повертає dict з деталями нарахувань.
    """
    from config import CASHBACK_OWN, CASHBACK_L1, CASHBACK_L2, REFERRAL_BONUS

    conn = await get_conn()
    result = {"own": 0, "l1": 0, "l2": 0, "referral_bonus": 0, "guest": None}

    try:
        async with conn.transaction():
            guest = await conn.fetchrow("SELECT * FROM guests WHERE id = $1", guest_id)
            if not guest:
                return None

            result["guest"] = guest
            is_first = not await has_first_spend(guest_id)

            # Записуємо витрату
            await conn.execute(
                "INSERT INTO spend_log (guest_id, amount, is_first, added_by) VALUES ($1, $2, $3, $4)",
                guest_id, spend_amount, is_first, admin_id
            )

            # 10% гостю
            own_points = int(spend_amount * CASHBACK_OWN / 100)
            if own_points > 0:
                await add_points(conn, guest_id, own_points, "cashback_own",
                                 f"Кешбек 10% від {spend_amount} грн")
                result["own"] = own_points

            # Рефер 1-го рівня (L1)
            if guest["referrer_id"]:
                l1_id = guest["referrer_id"]

                # Бонус за першу витрату
                if is_first:
                    await add_points(conn, l1_id, REFERRAL_BONUS, "referral_bonus",
                                     f"Бонус за першу витрату запрошеного {guest['full_name']}",
                                     related_guest_id=guest_id)
                    result["referral_bonus"] = REFERRAL_BONUS

                # 5% кешбек L1
                l1_points = int(spend_amount * CASHBACK_L1 / 100)
                if l1_points > 0:
                    await add_points(conn, l1_id, l1_points, "cashback_l1",
                                     f"5% від витрат {guest['full_name']} ({spend_amount} грн)",
                                     related_guest_id=guest_id)
                    result["l1"] = l1_points

                # Рефер 2-го рівня (L2)
                l1_guest = await conn.fetchrow("SELECT * FROM guests WHERE id = $1", l1_id)
                if l1_guest and l1_guest["referrer_id"]:
                    l2_id = l1_guest["referrer_id"]
                    l2_points = int(spend_amount * CASHBACK_L2 / 100)
                    if l2_points > 0:
                        await add_points(conn, l2_id, l2_points, "cashback_l2",
                                         f"2% від витрат {guest['full_name']} ({spend_amount} грн)",
                                         related_guest_id=guest_id)
                        result["l2"] = l2_points

    finally:
        await conn.close()

    return result


# --- ВИВЕДЕННЯ БАЛІВ ---

async def create_redeem_request(guest_id: int, amount: int):
    conn = await get_conn()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO redeem_requests (guest_id, amount)
            VALUES ($1, $2)
            RETURNING id
            """,
            guest_id, amount
        )
        return row["id"]
    finally:
        await conn.close()


async def get_pending_requests():
    conn = await get_conn()
    try:
        return await conn.fetch(
            """
            SELECT r.*, g.full_name, g.phone, g.username, g.balance
            FROM redeem_requests r
            JOIN guests g ON g.id = r.guest_id
            WHERE r.status = 'pending'
            ORDER BY r.created_at ASC
            """
        )
    finally:
        await conn.close()


async def approve_redeem(request_id: int, admin_id: int):
    """Підтвердити виведення - списати бали."""
    conn = await get_conn()
    try:
        async with conn.transaction():
            req = await conn.fetchrow(
                "SELECT * FROM redeem_requests WHERE id = $1 AND status = 'pending'",
                request_id
            )
            if not req:
                return None

            guest = await conn.fetchrow("SELECT * FROM guests WHERE id = $1", req["guest_id"])
            if guest["balance"] < req["amount"]:
                return "insufficient"

            # Списати бали
            await add_points(conn, req["guest_id"], -req["amount"], "redeem",
                             f"Виведення {req['amount']} балів (заявка #{request_id})")

            # Оновити статус заявки
            await conn.execute(
                """
                UPDATE redeem_requests
                SET status = 'approved', resolved_at = NOW(), resolved_by = $1
                WHERE id = $2
                """,
                admin_id, request_id
            )

            return req
    finally:
        await conn.close()


async def reject_redeem(request_id: int, admin_id: int):
    conn = await get_conn()
    try:
        req = await conn.fetchrow(
            "SELECT * FROM redeem_requests WHERE id = $1 AND status = 'pending'",
            request_id
        )
        if not req:
            return None

        await conn.execute(
            """
            UPDATE redeem_requests
            SET status = 'rejected', resolved_at = NOW(), resolved_by = $1
            WHERE id = $2
            """,
            admin_id, request_id
        )
        return req
    finally:
        await conn.close()


# --- АНУЛЮВАННЯ БАЛІВ ---

async def expire_points():
    """
    Анулювати бали гостей без активності 12+ місяців.
    Запускається щодня.
    """
    conn = await get_conn()
    expired_count = 0
    try:
        cutoff = datetime.utcnow() - timedelta(days=POINTS_EXPIRY_MONTHS * 30)
        guests = await conn.fetch(
            "SELECT * FROM guests WHERE last_activity < $1 AND balance > 0",
            cutoff
        )
        async with conn.transaction():
            for guest in guests:
                await add_points(conn, guest["id"], -guest["balance"], "expired",
                                 f"Анулювання балів через {POINTS_EXPIRY_MONTHS} місяців неактивності")
                expired_count += 1
    finally:
        await conn.close()
    return expired_count


# --- УТИЛІТИ ---

def normalize_phone(phone: str) -> str:
    """Привести номер до формату +380XXXXXXXXX."""
    digits = "".join(filter(str.isdigit, phone))
    if digits.startswith("380"):
        return "+" + digits
    elif digits.startswith("0") and len(digits) == 10:
        return "+38" + digits
    elif len(digits) == 9:
        return "+380" + digits
    return "+" + digits
