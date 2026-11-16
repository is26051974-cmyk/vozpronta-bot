import hashlib
import os

# Соль - чтобы хеш нельзя было подобрать по базе номеров
# Добавишь в Render позже: TRIAL_SALT = любое длинное слово, например VozPronta2026Secret
SALT = os.environ.get("TRIAL_SALT", "vozpronta-default-salt")

def hash_phone(phone):
    """Делает необратимый хеш номера. Номер обратно не восстановить."""
    text = f"{SALT}:{str(phone).strip()}"
    return hashlib.sha256(text.encode()).hexdigest()

def create_trial_table(conn):
    """Таблица тех, кто уже использовал пробный период. Ее BORRAR TODO НЕ удаляет."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trial_used (
            phone_hash TEXT PRIMARY KEY,
            first_trial_date TEXT
        )
    """)

def already_used_trial(conn, phone):
    """True - если этот номер уже брал 7 дней. Значит новый пробный не даем."""
    h = hash_phone(phone)
    cur = conn.execute(
        "SELECT 1 FROM trial_used WHERE phone_hash = ?",
        (h,)
    )
    return cur.fetchone() is not None

def mark_trial_used(conn, phone, date_str):
    """Записываем, что номер использовал пробный период. Вызывается 1 раз при первом сообщении."""
    h = hash_phone(phone)
    conn.execute(
        "INSERT OR IGNORE INTO trial_used (phone_hash, first_trial_date) VALUES (?, ?)",
        (h, date_str)
    )
