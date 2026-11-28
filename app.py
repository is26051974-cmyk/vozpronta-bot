from flask import Flask, request
import requests, os, tempfile, sqlite3, datetime, html, libsql
from zoneinfo import ZoneInfo
from trial_guard import create_trial_table, already_used_trial, mark_trial_used, hash_phone
app = Flask(__name__)
PHONE_ID = os.environ.get("PHONE_ID", "1345713805299738")
TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_TOKEN = TOKEN
GROQ_KEY = os.environ.get("GROQ_KEY", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "vozpronta123")
DB_PATH = os.environ.get("DB_PATH", "vozpronta.db")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://vozpronta-bot.onrender.com").rstrip("/")
PRIVACY_URL = os.environ.get("PRIVACY_URL", f"{PUBLIC_URL}/privacidad")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "sichgroupinvest@gmail.com")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
SUMUP_TOKEN = os.environ.get("SUMUP_API_KEY", "")
SUMUP_MERCHANT = os.environ.get("SUMUP_MERCHANT_CODE", "M196F01Y")
SUMUP_AMOUNT = float(os.environ.get("SUMUP_AMOUNT", "4.99"))
PAY_LINK = os.environ.get("PAY_LINK", "") or os.environ.get("SUMUP_PAYMENT_LINK", "") or "https://pay.sumup.com/p/s/XHKBFCSDLX"
ADMIN_PHONE = os.environ.get("ADMIN_PHONE", "")
WHATSAPP_PRIVACY_URL = "https://www.whatsapp.com/legal/privacy-policy"

CONSENT_TEXT = (
    "👋 ¡Hola! Soy VozPronta 10, un bot de transcripción.\n\n"
    " Funciono con la API oficial de WhatsApp (Meta) y convierto tus audios en texto en segundos.\n\n🔒 Tu privacidad está 100% protegida:\n• Solo actúo si me escribes: nunca te enviaré mensajes ni publicidad por mi cuenta.\n• Sin rastro: borro tu audio al instante tras transcribirlo.\n• Cero datos sensibles: jamás te pediré contraseñas ni datos bancarios.\n(Guardo solo tu contador de uso. Escribe BORRAR en cualquier momento para eliminar tus datos).\n\n🎙 ¡Pruébalo ahora! Reenvíame cualquier nota de voz y mira cómo funciona (tienes 7 días gratis)."
    "Nuestra prioridad es proteger tus datos. Puedes consultar cómo los tratamos en nuestra Política de Privacidad 👉 " + PRIVACY_URL + "\n"
    "y en la Política de privacidad de WhatsApp 👉 " + WHATSAPP_PRIVACY_URL + "\n\n"
    "¿Aceptas el uso del bot?"
)

TURSO_URL = os.environ.get("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")

def db():
    if TURSO_URL:
        conn = libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)
    else:
        conn = sqlite3.connect("vozpronta.db", timeout=10)
    conn.execute("CREATE TABLE IF NOT EXISTS consent (user TEXT PRIMARY KEY, created_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS stats (user TEXT PRIMARY KEY, count INTEGER DEFAULT 0, last_at TEXT, last_tasks TEXT, first_seen TEXT, daily_date TEXT, daily_count INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE IF NOT EXISTS paid_tx (tx_id TEXT PRIMARY KEY, phone TEXT, paid_at TEXT)")
    create_trial_table(conn)
    conn.commit()
    for col, ddl in [("first_seen", "ALTER TABLE stats ADD COLUMN first_seen TEXT"), ("daily_date", "ALTER TABLE stats ADD COLUMN daily_date TEXT"), ("daily_count", "ALTER TABLE stats ADD COLUMN daily_count INTEGER DEFAULT 0"), ("minutos", "ALTER TABLE stats ADD COLUMN minutos INTEGER DEFAULT 0")]:
        try:
            conn.execute(ddl)
        except Exception:
            pass
    try:
        conn.commit()
    except Exception:
        pass
    return conn

def has_consent(user):
    c = db(); r = c.execute("SELECT 1 FROM consent WHERE user=?", (user,)).fetchone(); c.close()
    return bool(r)

def save_consent(user):
    c = db(); c.execute("INSERT OR REPLACE INTO consent VALUES (?,?)", (user, datetime.datetime.now(ZoneInfo("Europe/Madrid")).isoformat())); c.commit(); c.close()

def remove_consent(user):
    c = db(); c.execute("DELETE FROM consent WHERE user=?", (user,)); c.commit(); c.close()

def delete_all_data(user):
    c = db()
    c.execute("DELETE FROM consent WHERE user=?", (user,))
    c.execute("DELETE FROM stats WHERE user=?", (user,))
    c.commit(); c.close()

def bump_counter(user, tasks_text="", minutos=0):
    c = db()
    now = datetime.datetime.now(ZoneInfo("Europe/Madrid"))
    today = now.strftime("%Y-%m-%d")
    row = c.execute("SELECT count, daily_date, daily_count, COALESCE(minutos,0) FROM stats WHERE user=?", (user,)).fetchone()
    n = (row[0] if row else 0) + 1
    total_min = (row[3] if row else 0) + (minutos or 0)
    old_daily_date = row[1] if row else None
    old_daily = row[2] or 0
    new_daily = 1 if old_daily_date != today else old_daily + 1
    if already_used_trial(c, user):
        _fs = (now - datetime.timedelta(days=8)).isoformat()
    else:
        mark_trial_used(c, user, today)
        _fs = now.isoformat()
    c.execute("INSERT INTO stats (user, count, last_at, last_tasks, first_seen, daily_date, daily_count, minutos) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(user) DO UPDATE SET count=excluded.count, last_at=excluded.last_at, last_tasks=excluded.last_tasks, daily_date=excluded.daily_date, daily_count=excluded.daily_count, minutos=excluded.minutos",
          (user, n, now.isoformat(), tasks_text[:2000], _fs, today, new_daily, total_min))
    c.commit(); c.close()
    return n, total_min

def get_my_stats(user):
    c = db(); r = c.execute("SELECT count, last_at, last_tasks, first_seen, daily_date, daily_count, COALESCE(minutos,0) FROM stats WHERE user=?", (user,)).fetchone(); c.close()
    return r

def create_sumup_checkout(user_phone):
    return PAY_LINK

def is_paid(user):
    c = db()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS paid_users (user TEXT PRIMARY KEY, paid_until TEXT)")
        row = c.execute("SELECT paid_until FROM paid_users WHERE user=?", (user,)).fetchone()
        if not row or not row[0]:
            return False
        until = datetime.datetime.fromisoformat(row[0])
        now = datetime.datetime.now(ZoneInfo("Europe/Madrid"))
        if until.tzinfo is None:
            until = until.replace(tzinfo=ZoneInfo("Europe/Madrid"))
        return until > now
    except Exception:
        return False
    finally:
        c.close()

def activate_paid(user, days=30):
    c = db()
    c.execute("CREATE TABLE IF NOT EXISTS paid_users (user TEXT PRIMARY KEY, paid_until TEXT)")
    until = (datetime.datetime.now(ZoneInfo("Europe/Madrid")) + datetime.timedelta(days=days)).isoformat()
    c.execute("INSERT OR REPLACE INTO paid_users (user, paid_until) VALUES (?,?)", (user, until))
    c.commit(); c.close()
    return until

def check_sumup_payment(user):
    token = os.environ.get("SUMUP_API_KEY") or os.environ.get("SUMUP_TOKEN") or ""
    if not token:
        print("[SUMUP] no token", flush=True)
        return None
    try:
        r = requests.get("https://api.sumup.com/v0.1/me/transactions/history", headers={"Authorization": f"Bearer {token}"}, params={"limit": 20}, timeout=10)
        print(f"[SUMUP] history {r.status_code}", flush=True)
        if r.status_code != 200:
            print(f"[SUMUP] history body: {r.text[:300]}", flush=True)
            return None
        data = r.json()
        items = data.get("items", []) if isinstance(data, dict) else data
        now = datetime.datetime.now(datetime.timezone.utc)
        for tx in items:
            amount = tx.get("amount", 0)
            status = str(tx.get("status", "")).upper()
            tx_id = str(tx.get("transaction_code") or tx.get("id") or tx.get("transaction_id") or "")
            ts = tx.get("timestamp") or tx.get("created_at") or ""
            if not tx_id:
                continue
            try:
                amt = float(amount)
                amt_cents = int(round(amt * 100)) if amt < 100 else int(amt)
            except Exception:
                continue
            if amt_cents != 499:
                continue
            if status not in ("SUCCESSFUL", "SUCCESS", "PAID", "COMPLETE", "COMPLETED"):
                continue
            try:
                tx_time = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if now - tx_time > datetime.timedelta(days=7):
                    continue
            except Exception:
                pass
            c = db()
            row = c.execute("SELECT tx_id FROM paid_tx WHERE tx_id = ?", (tx_id,)).fetchone()
            c.close()
            if row:
                continue
            return tx_id
        return None
    except Exception as e:
        print(f"[SUMUP] check error: {e}", flush=True)
        return None

def check_limit(user):
    c = db()
    now = datetime.datetime.now(ZoneInfo("Europe/Madrid"))
    today = now.strftime("%Y-%m-%d")
    row = c.execute("SELECT first_seen, daily_date, daily_count FROM stats WHERE user=?", (user,)).fetchone()
    if not row or not row[0]:
        if already_used_trial(c, user):
            old = (now - datetime.timedelta(days=8)).isoformat()
            if not row:
                c.execute("INSERT INTO stats (user, count, first_seen, daily_date, daily_count) VALUES (?,?,?,?,?)", (user, 0, old, today, 0))
            else:
                c.execute("UPDATE stats SET first_seen=? WHERE user=?", (old, user))
            c.commit(); c.close()
            return (True, 0, 0)
        else:
            mark_trial_used(c, user, today)
            c.execute("INSERT INTO stats (user, count, first_seen, daily_date, daily_count) VALUES (?,?,?,?,?) ON CONFLICT(user) DO UPDATE SET first_seen=excluded.first_seen", (user, 0, now.isoformat(), today, 0))
            c.commit(); c.close()
            return (True, 7, 0)
    first_seen = datetime.datetime.fromisoformat(row[0])
    if first_seen.tzinfo is None:
        first_seen = first_seen.replace(tzinfo=ZoneInfo("Europe/Madrid"))
    days_passed = (now - first_seen).days
    trial_left = max(0, 7 - days_passed)
    daily_date = row[1]
    daily_count = row[2] or 0
    if daily_date != today:
        c.execute("UPDATE stats SET daily_date=?, daily_count=0 WHERE user=?", (today, user))
        c.commit()
        daily_count = 0
    c.close()
    if is_paid(user):
        return (True, 0, 0)
    if days_passed < 7:
        return (True, trial_left, daily_count)
    if daily_count >= 5:
        return (False, 0, daily_count)
    return (True, 0, daily_count)

TASKS_MODEL_PRIMARY = "openai/gpt-oss-120b"
TASKS_MODEL_FALLBACK = "openai/gpt-oss-20b"
TASKS_PROMPT = (
    "Eres un asistente que extrae solo tareas concretas con fecha si la hay. Responde SIEMPRE en el mismo idioma del texto del usuario. No traduzcas. Si el texto es en ruso, responde en ruso. Si es en ucraniano, responde en ucraniano. Si es en español, responde en español. Si no hay tareas, responde en el mismo idioma del texto: 'Sin tareas concretas.' en español, 'Нет конкретных задач.' en ruso, 'Немає конкретних завдань.' en ucraniano."
    "Devuelve una lista con vinetas, una tarea por linea, empezando cada linea con '- '. "
    "Reglas estrictas: 1) Sin introducciones, sin saludos, sin repetir todo el texto. 2) NUNCA inventes tareas, nombres, fechas, cifras o monedas que no esten en el texto. 3) Conserva nombres, fechas, horas, cifras y monedas TAL CUAL, sin traducir ni redondear. 4) Cada tarea = verbo en infinitivo + que + cuando/quien, si esta en el texto. 5) Si es solo una opinion, recuerdo o comentario sin accion, NO es tarea. 6) Maximo 10 tareas, las mas importantes primero. 7) Si no hay ninguna tarea clara, devuelve EXACTAMENTE una linea: '- Sin tareas concretas.'"
)

def _call_tasks_model(model, text):
    r = requests.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
        json={"model": model, "temperature": 0.0, "max_tokens": 600, "messages": [{"role": "system", "content": TASKS_PROMPT}, {"role": "user", "content": text[:6000]}]}, timeout=30)
    print(f"tasks {model}: {r.status_code} {r.text[:500]}", flush=True)
    if r.status_code != 200:
        return ""
    return (r.json().get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()

def extract_tasks(text):
    if not GROQ_KEY or not text.strip():
        return "- Sin tareas concretas."
    out = ""
    for model in (TASKS_MODEL_PRIMARY, TASKS_MODEL_FALLBACK):
        try:
            out = _call_tasks_model(model, text)
            if out:
                break
        except Exception as e:
            print(f"tasks error {model}: {e}", flush=True)
            continue
    if not out:
        return "- Sin tareas concretas."
    lines = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("- "):
            lines.append(line)
        elif len(lines) == 0 and line.lower().startswith("sin tareas"):
            lines.append("- Sin tareas concretas.")
    if not lines:
        return "- Sin tareas concretas."
    return "\n".join(lines[:10])

DATOS_PROMPT = (
    "Eres un extractor de datos. Extrae SOLO lo que esta literalmente en el texto. "
    "Responde SIEMPRE en el mismo idioma del texto. No traduzcas. "
    "Busca solo: fecha, hora, importe / dinero, telefono, IBAN / cuenta, direccion, codigo, nombre de persona si va con dato. "
    "Reglas estrictas: 1) NUNCA inventes, no completes, no redondees, no corrijas. 2) Copia TAL CUAL como esta en el texto. 3) Si un dato esta incompleto o dudoso, pon [?] al final. 4) Cada dato en una linea: '- `VALOR` - tipo'. 5) Si NO hay ningun dato, responde EXACTAMENTE: 'SIN_DATOS'. 6) Maximo 8 datos."
)
def extract_datos(text):
    if not GROQ_KEY or not text.strip():
        return ""
    out = ""
    for model in (TASKS_MODEL_PRIMARY, TASKS_MODEL_FALLBACK):
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                json={"model": model, "temperature": 0.0, "max_tokens": 300, "messages": [{"role": "system", "content": DATOS_PROMPT}, {"role": "user", "content": text[:6000]}]}, timeout=30)
            if r.status_code == 200:
                out = (r.json().get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
                if out:
                    break
        except Exception as e:
            print(f"datos error {model}: {e}", flush=True)
            continue
    if not out or "SIN_DATOS" in out.upper():
        return ""
    lines = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("- "):
            lines.append(line)
    return "\n".join(lines[:8])

# --- BLOK: OPLATA CHEREZ KNOPKU, BEZ GOLOY SSYLKI ---
def send_paywall(to, user_lang="es"):
    texts = {
        "es": {"title": "Prueba gratuita terminada", "body": "Tu prueba de 7 días ha terminado.\n\nSigue con transcripciones ilimitadas, traducción y respuestas listas por solo 4,99 €/mes. Cancela cuando quieras.", "button": "Suscribirse - 4,99 €"},
        "ru": {"title": "Пробный период закончился", "body": "Твои 7 бесплатных дней закончились.\n\nПродолжай с безлимитными расшифровками, переводом и готовыми ответами всего за 4,99 €/мес. Отмена в любой момент.", "button": "Оформить - 4,99 €"},
        "uk": {"title": "Пробний період закінчився", "body": "Твої 7 безкоштовних днів закінчилися.\n\nПродовжуй з безлімітними розшифровками, перекладом і готовими відповідями всього за 4,99 €/міс. Скасування в будь-який момент.", "button": "Оформити - 4,99 €"},
    }
    t = texts.get(user_lang, texts["es"])
    payload = {"messaging_product": "whatsapp", "to": to, "type": "interactive", "interactive": {"type": "cta_url", "body": {"text": f"*{t['title']}*\n\n{t['body']}"}, "action": {"name": "cta_url", "parameters": {"display_text": t["button"], "url": PAY_LINK}}}}
    try:
        r = requests.post(f"https://graph.facebook.com/v21.0/{PHONE_ID}/messages", headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}, json=payload, timeout=15)
        print(f"PAYWALL to {to}: {r.status_code} {r.text[:300]}", flush=True)
    except Exception as e:
        print(f"send_paywall error: {e}", flush=True)

@app.route('/')
def home():
    return "VozPronta 10 - Bot is Live!"

PRIVACY_HTML = f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Política de Privacidad - VozPronta 10</title><style>body{{font-family:system-ui,sans-serif;max-width:760px;margin:32px auto;padding:0 16px;line-height:1.6}}</style></head><body><h1>🔒 Política de Privacidad - VozPronta 10</h1><p><b>Última actualización:</b> 12 de noviembre de 2026</p><p>Contacto: <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a></p><h2>Qué datos recogemos</h2><ul><li>Tu número de WhatsApp - solo para responderte.</li><li>Texto de tu última transcripción y tus tareas.</li><li>Tu contador y fecha de la última transcripción.</li><li>Tu consentimiento.</li></ul><h2>Qué NO guardamos</h2><ul><li>NO guardamos tus audios.</li><li>NO pedimos contraseñas ni datos bancarios.</li></ul><p>Escribe <code>BORRAR</code> para borrar todo, <code>PRIVACIDAD</code> para ver esta política.</p></body></html>"""

@app.route('/privacidad')
@app.route('/politica-privacidad')
def privacidad():
    return PRIVACY_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}

def _mask_phone(user):
    u = str(user or "")
    return "****" + u[-4:] if len(u) > 4 else "****"

@app.route('/admin')
def admin():
    key = request.args.get("key", "")
    if not ADMIN_KEY or key != ADMIN_KEY:
        return "403 - Acceso denegado.", 403
    try:
        c = db()
        total_consent = c.execute("SELECT COUNT(*) FROM consent").fetchone()[0]
        row = c.execute("SELECT COUNT(*), COALESCE(SUM(count),0) FROM stats").fetchone()
        total_users, total_trans = row[0], row[1]
        today = datetime.datetime.now(ZoneInfo("Europe/Madrid")).strftime("%Y-%m-%d")
        today_users = c.execute("SELECT COUNT(*) FROM stats WHERE last_at LIKE ?", (today + "%",)).fetchone()[0]
        users = c.execute("SELECT user, count, last_at, last_tasks FROM stats ORDER BY last_at DESC LIMIT 50").fetchall()
        c.close()
    except Exception as e:
        return f"Error admin: {e}", 500
    rows_html = ""
    for u, cnt, last_at, last_tasks in users:
        rows_html += f"<tr><td>{html.escape(_mask_phone(u))}</td><td>{cnt}</td><td>{html.escape(str(last_at or '-'))}</td><td>{html.escape((last_tasks or '-')[:300])}</td></tr>"
    if not rows_html:
        rows_html = "<tr><td colspan='4'>Aún no hay transcripciones.</td></tr>"
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Admin - VozPronta 10</title></head><body><h1>Admin</h1><p>Consent: {total_consent} | Users: {total_users} | Trans: {total_trans} | Hoy: {today_users}</p><table border='1'><tr><th>Usuario</th><th>Trans</th><th>Última</th><th>Tareas</th></tr>{rows_html}</table></body></html>", 200, {"Content-Type": "text/html; charset=utf-8"}

@app.route('/webhook', methods=['GET'])
def verify():
    challenge = request.args.get("hub.challenge")
    token = request.args.get("hub.verify_token")
    if token == VERIFY_TOKEN and challenge:
        return challenge, 200
    if challenge:
        return challenge, 200
    return "ok", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True) or {}
        print(f"POST WEBHOOK: {data}", flush=True)
        val = data.get('entry', [{}])[0].get('changes', [{}])[0].get('value', {})
        if 'messages' not in val:
            return "OK", 200
        m = val['messages'][0]
        frm = m['from']
        typ = m['type']
        def send_payload(payload):
            url = f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages"
            h = {"Authorization": f"Bearer {TOKEN}"}
            r = requests.post(url, headers=h, json=payload)
            print(f"SEND to {frm}: {r.status_code} {r.text[:500]}", flush=True)
            return r
        def send(t):
            return send_payload({"messaging_product": "whatsapp", "to": frm, "text": {"body": t}})
        def send_consent():
            return send_payload({"messaging_product": "whatsapp", "to": frm, "type": "interactive", "interactive": {"type": "button", "body": {"text": CONSENT_TEXT}, "action": {"buttons": [{"type": "reply", "reply": {"id": "consent_accept", "title": "✅ Sí, acepto"}}, {"type": "reply", "reply": {"id": "consent_decline", "title": "❌ No acepto"}}]}}})
        if typ == "interactive":
            btn_id = m.get('interactive', {}).get('button_reply', {}).get('id', '')
            if btn_id == "consent_accept":
                save_consent(frm)
                send("✅ ¡Gracias! Consentimiento aceptado.\n\n🎙 Ahora envía un audio de hasta 10 min y te lo transcribo.")
            elif btn_id == "consent_decline":
                remove_consent(frm)
                send("❌ Entendido. No usaré el bot contigo.\nSi cambias de opinión, escribe \"Hola\" y vuelve a aceptar.")
            return "OK", 200
        if typ == "text":
            txt = m['text']['body'].strip()
            print(f"TEXT from {frm}: {txt}", flush=True)
            up = txt.upper()
            PENDING = globals().setdefault("PENDING_BORRAR", {})
            # TEST_PAYWALL - pered PAGAR, inache is_paid vernet ranshe
            if up == "TEST_PAYWALL" and ADMIN_PHONE and frm == ADMIN_PHONE:
                send_paywall(frm, "ru")
                return "OK", 200
            if up == "PAGAR":
                if is_paid(frm):
                    send("✅ Tu suscripcion ya esta activa.\n\nEnvia un audio y te lo transcribo sin limites.")
                    return "OK", 200
                tx_id = check_sumup_payment(frm)
                if tx_id:
                    c = db()
                    c.execute("INSERT OR IGNORE INTO paid_tx (tx_id, phone, paid_at) VALUES (?, ?, ?)", (tx_id, frm, datetime.datetime.now(datetime.timezone.utc).isoformat()))
                    c.commit(); c.close()
                    activate_paid(frm, days=30)
                    send("✅ Pago recibido! Tu suscripcion esta activada por 30 dias.\n\nEnvia un audio y te lo transcribo sin limites.")
                else:
                    send("⏳ Aún no veo tu pago.\n\nSi ya pagaste, espera 1 minuto y escribe PAGAR otra vez.\n\nSi aún no pagaste, pulsa el botón abajo 👇")
                    send_paywall(frm, "es")
                return "OK", 200
            if up in ("BORRAR", "BORRAR MIS DATOS", "ELIMINAR MIS DATOS", "DELETE"):
                PENDING[frm] = "menu"
                send("¿Qué quieres borrar?\n\n1. Escribe BORRAR CHAT - solo borro este chat\n2. Escribe BORRAR TODO - borro TODO\n\nEscribe 1 o 2, o CANCELAR")
                return "OK", 200
            if up == "BORRAR CHAT" or (up == "1" and PENDING.get(frm) == "menu"):
                PENDING.pop(frm, None)
                send("Listo. Borré solo este chat. Tu contador y tus días gratis siguen intactos.")
                return "OK", 200
            if up == "BORRAR TODO" or (up == "2" and PENDING.get(frm) == "menu"):
                PENDING[frm] = "confirm_todo"
                send("¿Seguro? Si borras TODO perderás tu contador y tu periodo gratis. Escribe SI BORRAR TODO para confirmar, o CANCELAR para dejarlo.")
                return "OK", 200
            if PENDING.get(frm) == "confirm_todo" and up in ("SI BORRAR TODO", "TODO", "SI", "SÍ", "YES", "2"):
                PENDING.pop(frm, None)
                delete_all_data(frm)
                send("Listo. Borré TODO: tu consentimiento, tu contador y tus últimas tareas.")
                return "OK", 200
            if up == "CANCELAR":
                PENDING.pop(frm, None)
                send("Ok, no borré nada. Todo sigue como estaba.")
                return "OK", 200
            if up in ("NO", "NO ACEPTO", "STOP", "BAJA"):
                remove_consent(frm)
                send("❌ Consentimiento retirado. Ya no procesaré tus audios.\nTus datos se pueden borrar por completo escribiendo BORRAR.\nEscribe \"Hola\" para volver a aceptar.")
                return "OK", 200
            if "@" in txt and "." in txt and len(txt) < 100:
                _c = db()
                _c.execute("CREATE TABLE IF NOT EXISTS paid_users (user TEXT PRIMARY KEY, email TEXT, paid_until TEXT)")
                _c.execute("INSERT OR REPLACE INTO paid_users (user, email, paid_until) VALUES (?,?,?)", (frm, txt.lower().strip(), "PENDING"))
                _c.commit(); _c.close()
                send("Recibido! Estoy verificando tu pago en SumUp.\nSi el pago esta confirmado, te activo en unas horas y te aviso aqui.")
                return "OK", 200
            if up in ("MIS ESTADISTICAS", "MIS ESTADÍSTICAS", "ESTADISTICAS", "ESTADÍSTICAS", "MI CONTADOR", "STATS"):
                row = get_my_stats(frm)
                if not row:
                    send("📊 Aún no tienes transcripciones.\nEnvía un audio y aquí aparecerá tu contador.")
                else:
                    now_mad = datetime.datetime.now(ZoneInfo("Europe/Madrid"))
                    today_mad = now_mad.strftime("%Y-%m-%d")
                    first_seen = row[3]; daily_date = row[4]; daily_count = row[5] or 0
                    if daily_date != today_mad:
                        daily_count = 0
                    if is_paid(frm):
                        trial_line = "✅ Suscripción activa · sin límites"
                    elif not first_seen:
                        _c = db()
                        if already_used_trial(_c, frm):
                            trial_line = "💳 Prueba terminada. Hoy: 0 / 5 gratuitas"
                        else:
                            mark_trial_used(_c, frm, today_mad); _c.commit(); trial_line = "🎁 Prueba gratuita: 7 días"
                        _c.close()
                    else:
                        fs = datetime.datetime.fromisoformat(first_seen)
                        if fs.tzinfo is None:
                            fs = fs.replace(tzinfo=ZoneInfo("Europe/Madrid"))
                        days_passed = (now_mad - fs).days
                        trial_line = f"🎁 Prueba gratuita: quedan {7 - days_passed} días" if days_passed < 7 else f"📅 Prueba terminada. Hoy: {daily_count} / 5 gratuitas"
                    send(f"📊 Tus estadísticas:\n• Transcripciones: {row[0]}\n• Última: {row[1][:16] if row[1] else '-'}\n• {trial_line}\n• ⏱ En total te ahorré ~{row[6]} min de escucha.\n\n📋 Últimas tareas:\n{row[2] or '• Sin tareas concretas.'}")
                return "OK", 200
            if not has_consent(frm):
                send_consent()
                return "OK", 200
            send("🎙 Estoy aquí! Reenvíame ese audio que te da pereza escuchar y te lo dejo clarito en 10 segundos.\n\nEscribe \"MIS ESTADISTICAS\" para ver tu contador.")
            return "OK", 200
        if typ in ("audio", "voice"):
            if not has_consent(frm):
                send_consent()
                send("⚠ Primero pulsa \"✅ Sí, acepto\" arriba y vuelve a enviar el audio.")
                return "OK", 200
            allowed, trial_left, daily_n = check_limit(frm)
            if not allowed:
                if is_paid(frm):
                    pass
                else:
                    link = create_sumup_checkout(frm)
                    send(f"Has usado tus 5 transcripciones gratuitas de hoy.\n\nTu semana gratuita ya termino.\n\nPara seguir sin limites:\n\n1 mes - {SUMUP_AMOUNT:.2f} EUR")
                    send_paywall(frm, "es")
                    return "OK", 200
            aid = m['audio']['id']
            info = requests.get(f"https://graph.facebook.com/v19.0/{aid}", headers={"Authorization": f"Bearer {TOKEN}"}).json()
            if 'url' not in info:
                send("❌ Error al descargar el audio")
                return "OK", 200
            aud = requests.get(info['url'], headers={"Authorization": f"Bearer {TOKEN}"}).content
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf:
                tf.write(aud); tf.flush(); path = tf.name
                with open(path, 'rb') as f:
                    r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions", headers={"Authorization": f"Bearer {GROQ_KEY}"}, files={"file": f}, data={"model": "whisper-large-v3"})
                os.unlink(path)
            if r.status_code == 200:
                text = r.json().get('text', '')
                tasks = extract_tasks(text)
                datos = extract_datos(text)
                palabras = len(text.split())
                ahorrado = max(1, round(palabras / 150))
                n, total_min = bump_counter(frm, tasks, ahorrado)
                intro = "Uff, este audio era un rollo 😅 Te lo dejé clarito, mira:" if len(text) > 400 else "Listo, ya lo tienes 👇"
                msg = f"{intro}\n\n{text}"
                if datos:
                    msg += f"\n\n⚡ Datos clave:\n{datos}"
                msg += f"\n\n📋 Tareas:\n{tasks or '- Sin tareas concretas.'}"
                msg += f"\n\n⏱ Te ahorré ~{ahorrado} min de escucha."
                msg += f"\n\n📊 Esta es tu transcripción #{n}."
                send(msg[:3900])
            else:
                send(f"❌ Error: {r.text[:300]}")
    except Exception as e:
        print(e)
    return "OK", 200

SUMUP_CLIENT_ID = os.environ.get("SUMUP_CLIENT_ID", "")
SUMUP_CLIENT_SECRET = os.environ.get("SUMUP_CLIENT_SECRET", "")

@app.route('/sumup-callback')
def sumup_callback():
    code = request.args.get("code", "")
    print(f"SUMUP CALLBACK CODE: {code}", flush=True)
    if not code:
        return "No code", 400
    try:
        r = requests.post("https://api.sumup.com/token", json={"grant_type": "authorization_code", "client_id": SUMUP_CLIENT_ID, "client_secret": SUMUP_CLIENT_SECRET, "code": code, "redirect_uri": f"{PUBLIC_URL}"}, timeout=15)
        print(f"SUMUP TOKEN: {r.status_code} {r.text[:500]}", flush=True)
        data = r.json()
        if "access_token" in data:
            return "SumUp conectado! Puedes cerrar esta ventana y volver a WhatsApp.", 200
    except Exception as e:
        print(f"sumup callback error: {e}", flush=True)
    return "SumUp callback recibido, revisa logs", 200

@app.route('/sumup-webhook', methods=['POST'])
def sumup_webhook():
    try:
        data = request.get_json(force=True, silent=True) or {}
        print(f" SUMUP WEBHOOK: {data}", flush=True)
        ref = data.get("checkout_reference") or data.get("reference") or ""
        status = str(data.get("status") or data.get("payload", {}).get("status") or "").upper()
        if ref and ("PAID" in status or "SUCCESS" in status or "COMPLETE" in status or status == ""):
            until = activate_paid(ref, days=30)
            print(f" SUMUP ACTIVATED {ref} until {until}", flush=True)
            try:
                url = f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages"
                requests.post(url, headers={"Authorization": f"Bearer {TOKEN}"}, json={"messaging_product": "whatsapp", "to": ref, "text": {"body": "✅ Pago recibido! Tu suscripcion esta activada por 30 dias.\n\nEnvia un audio y te lo transcribo sin limites."}}, timeout=10)
            except Exception as e:
                print(f" notify paid error: {e}", flush=True)
        return "OK", 200
    except Exception as e:
        print(f" sumup webhook error: {e}", flush=True)
        return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
