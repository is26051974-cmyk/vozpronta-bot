import os
import stripe

# Ключ берем только из Render Environment, в коде его НЕТ
# В Render добавишь: STRIPE_SECRET_KEY = sk_test_...
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

# Твой реальный Price ID из Stripe, Test Mode
PRICE_ID = "price_1UcOhG2V7NY9j60Ht36xB5p2"
PRICE_EUR = 2.99

def is_paid(user_id):
    # Пока заглушка - позже будем читать из базы
    # Возвращает True если у пользователя активна подписка
    return False

def create_checkout_link(user_id):
    """Создает ссылку на оплату для конкретного пользователя"""
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{"price": PRICE_ID, "quantity": 1}],
            success_url="https://wa.me/34600000000?text=PAGADO",
            cancel_url="https://wa.me/34600000000?text=CANCELADO",
            client_reference_id=str(user_id),
            metadata={"user_id": str(user_id)}
        )
        return session.url
    except Exception as e:
        print(f"Stripe error: {e}")
        return None

def handle_webhook(payload, sig_header, endpoint_secret):
    """Обрабатывает webhook от Stripe когда оплата прошла"""
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except Exception as e:
        print(f"Webhook error: {e}")
        return False

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session.get("client_reference_id")
        if user_id:
            mark_as_paid(user_id)
            return True
    return False

def mark_as_paid(user_id):
    """Помечает пользователя как оплатившего - позже подключим к Turso"""
    print(f"User {user_id} PAID - VozPronta Bezlimit 30 days")
    return True
