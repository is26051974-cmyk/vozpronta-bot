
# payment.py - ОТДЕЛЬНЫЙ МОДУЛЬ ОПЛАТЫ
# НЕ подключен к app.py ! Прод не трогаем.
# Режим: Stripe TEST MODE - настоящие деньги не списываются
#
# Что нужно от тебя для теста:
# 1. Зайти на https://dashboard.stripe.com -> Developers -> API keys
# 2. Взять TEST ключи: pk_test_... и sk_test_...
# 3. В Render -> Environment добавить:
#    STRIPE_SECRET_KEY = sk_test_...
#    STRIPE_PRICE_ID = price_... (создадим ниже)
#    STRIPE_WEBHOOK_SECRET = whsec_... (потом)

import os
import time

# Цена - меняем в одном месте
PRICE_EUR = 2.99
TRIAL_DAYS = 7
PAID_DAYS = 30  # оплатил -> безлимит на 30 дней

# --- Stripe ---
# pip install stripe -> добавить в requirements.txt ТОЛЬКО когда будем склеивать
try:
    import stripe
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_ENABLED = bool(stripe.api_key)
except ImportError:
    stripe = None
    STRIPE_ENABLED = False

# --- Таблица оплат (создадим при склейке, сейчас только функции) ---
# CREATE TABLE IF NOT EXISTS payments (
#   user TEXT PRIMARY KEY,
#   paid_until INTEGER,  -- unix timestamp, до какого числа оплачено
#   stripe_customer_id TEXT,
#   stripe_session_id TEXT,
#   updated_at INTEGER
# );

def is_paid(conn, user: str) -> bool:
    """Проверка: оплатил ли юзер? True = безлимит, не проверяем check_limit"""
    if not conn:
        return False
    try:
        cur = conn.execute(
            "SELECT paid_until FROM payments WHERE user=?",
            (user,)
        )
        row = cur.fetchone()
        if not row:
            return False
        return int(row[0]) > int(time.time())
    except Exception:
        # таблицы payments еще нет (мы же не склеили) -> считаем не оплатил
        return False

def mark_as_paid(conn, user: str, days: int = PAID_DAYS, stripe_customer_id: str = "", stripe_session_id: str = ""):
    """Помечаем юзера как оплаченного на N дней. Вызывается из webhook."""
    if not conn:
        return
    now = int(time.time())
    # Если уже был оплачен и еще не кончилось - продлеваем от конца, а не от сегодня
    cur = conn.execute("SELECT paid_until FROM payments WHERE user=?", (user,))
    row = cur.fetchone()
    base = max(now, int(row[0]) if row else now)
    new_until = base + days * 86400
    conn.execute(
        """INSERT INTO payments (user, paid_until, stripe_customer_id, stripe_session_id, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(user) DO UPDATE SET
             paid_until=excluded.paid_until,
             stripe_customer_id=excluded.stripe_customer_id,
             stripe_session_id=excluded.stripe_session_id,
             updated_at=excluded.updated_at""",
        (user, new_until, stripe_customer_id, stripe_session_id, now)
    )
    conn.commit()

def create_checkout_link(user: str, success_url: str, cancel_url: str) -> str:
    """
    Создает ссылку на оплату Stripe Checkout.
    Возвращает URL, который бот отправит юзеру в WhatsApp.
    В TEST MODE оплата идет картой 4242 4242 4242 4242
    """
    if not STRIPE_ENABLED:
        return "STRIPE_NO_CONFIGURADO"

    price_id = os.environ.get("STRIPE_PRICE_ID", "")
    if not price_id:
        return "STRIPE_NO_PRICE_ID"

    session = stripe.checkout.Session.create(
        mode="payment",  # разовый платеж 2.99, не подписка - проще для старта
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=cancel_url,
        client_reference_id=user,  # сюда кладем номер телефона, чтобы webhook понял кто оплатил
        metadata={"whatsapp_user": user, "product": "vozpronta_unlimited_30d"},
    )
    return session.url

def handle_webhook(payload: bytes, sig_header: str, conn) -> bool:
    """
    Вызывается из app.py когда Stripe шлет webhook checkout.session.completed
    Возвращает True если это была оплата и мы отметили юзера.
    """
    if not stripe:
        return False
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception:
        return False

    if event["type"] != "checkout.session.completed":
        return False

    session = event["data"]["object"]
    if session.get("payment_status") != "paid":
        return False

    user = session.get("client_reference_id") or session.get("metadata", {}).get("whatsapp_user")
    if not user:
        return False

    mark_as_paid(
        conn,
        user,
        days=PAID_DAYS,
        stripe_customer_id=session.get("customer", ""),
        stripe_session_id=session.get("id", ""),
    )
    return True

# --- Тексты для бота (когда будем склеивать) ---
PAYMENT_MSG = (
    "🚀 *Has llegado a tu límite gratuito*\n\n"
    "Tu prueba de 7 días terminó.\n"
    "Para seguir sin límites por 30 días: *2,99€*\n\n"
    "👉 Paga aquí: {link}\n\n"
    "Después del pago, vuelve aquí y escribe *PAGADO*"
)

PAID_OK_MSG = "✅ *Pago confirmado!* Tienes 30 días sin límites. ¡A transcribir! 🎙️"
