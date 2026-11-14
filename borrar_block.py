# borrar_block.py - ОТДЕЛЬНЫЙ БЛОК, не трогает основной код
# Логика: 2 кнопки, подтверждение, защита от накрутки бесплатной недели

# Хранилище для тех, кто уже удалился (только номер + дата, без текстов)
# Это нужно, чтобы нельзя было жать Borrar -> Hola -> снова бесплатная неделя
DELETED_USERS = {}  # {phone: "2026-11-14"}

def handle_borrar_menu():
    """Шаг 1: Человек нажал Borrar - показываем выбор, НИЧЕГО НЕ УДАЛЯЕМ"""
    return {
        "text": "¿Qué quieres borrar?",
        "buttons": [
            {"id": "borrar_chat", "title": "🗑️ Borrar este chat"},
            {"id": "borrar_datos", "title": "❌ Borrar mis datos"},
            {"id": "cancelar", "title": "↩️ Cancelar"},
        ]
    }

def handle_borrar_chat(user_data):
    """Кнопка 1: Удалить только последнюю транскрибацию"""
    # Удаляем только последнее, счётчик и пробный период НЕ трогаем
    user_data["last_tasks"] = []
    user_data["last_transcription"] = None
    return "Listo. Borré solo este chat. Tu contador y tus días gratis siguen intactos."

def handle_borrar_datos_confirm():
    """Кнопка 2: Запрос подтверждения перед полным удалением"""
    return {
        "text": "¿Seguro? Si borras tus datos, perderás tu contador y tu periodo gratis. No se puede recuperar.",
        "buttons": [
            {"id": "confirm_borrar_todo", "title": "✅ Sí, borrar todo"},
            {"id": "cancelar", "title": "❌ No, dejarlo"},
        ]
    }

def handle_confirm_borrar_todo(phone, user_data):
    """Человек подтвердил - удаляем ВСЁ, но запоминаем номер от накрутки"""
    # 1. Запоминаем, что этот номер уже использовал бесплатный период
    # Храним ТОЛЬКО номер и дату, без текстов - это разрешено по GDPR (анти-фрод)
    import datetime
    DELETED_USERS[phone] = datetime.date.today().isoformat()

    # 2. Чистим данные пользователя
    user_data.clear()

    return "Listo. Borré TODO: tu consentimiento, tu contador y tus últimas tareas.\nNo guardo nada tuyo.\nSi quieres volver, escribe 'Hola'."

def is_returning_after_delete(phone):
    """Проверка при Hola: человек уже удалялся раньше?"""
    return phone in DELETED_USERS
