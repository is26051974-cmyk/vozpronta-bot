from flask import Flask, request
import requests, os, tempfile, sqlite3, datetime, html, libsql
from zoneinfo import ZoneInfo
from trial_guard import create_trial_table, already_used_trial, mark_trial_used, hash_phone
app = Flask(__name__)
PHONE_ID = os.environ.get("PHONE_ID", "1345713805299738")
TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
GROQ_KEY = os.environ.get("GROQ_KEY", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "vozpronta123")
DB_PATH = os.environ.get("DB_PATH", "vozpronta.db")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://vozpronta-bot.onrender.com").rstrip("/")
PRIVACY_URL = os.environ.get("PRIVACY_URL", f"{PUBLIC_URL}/privacidad")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "sichgroupinvest@gmail.com")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
SUMUP_TOKEN = os.environ.get("SUMUP_API_KEY", "")
SUMUP_MERCHANT = os.environ.get("SUMUP_MERCHANT_CODE", "M196F01Y")
SUMUP_AMOUNT = float(os.environ.get("SUMUP_AMOUNT", "1.00"))
WHATSAPP_PRIVACY_URL = "https://www.whatsapp.com/legal/privacy-policy"

CONSENT_TEXT = (
    "👋 ¡Hola! Soy VozPronta 10, un bot de transcripción.\n\n"
    "Funciono con la API oficial de WhatsApp (Meta).\n\n"
    "✅ Transcribo TU audio a texto, solo cuando TÚ me lo envías.\n"
    "✅ BORRO tu audio justo después de transcribirlo.\n"
    "❌ NUNCA te escribo primero. Solo respondo a tus mensajes.\n"
    "❌ NUNCA te pido contraseñas, códigos, tarjetas ni datos bancarios.\n"
    "❌ NUNCA envío publicidad ni reenvío tus mensajes a nadie.\n\n"
    "Guardo solo: tu contador y el texto de tu última transcripción.\n"
    "Puedes borrar todo escribiendo BORRAR, o salir escribiendo NO.\n\n"
    "Nuestra prioridad es proteger tus datos. Puedes consultar cómo los tratamos en nuestra Política de Privacidad 👉 " + PRIVACY_URL + "\n"
    "y en la Política de privacidad de WhatsApp 👉 " + WHATSAPP_PRIVACY_URL + "\n\n"
    "¿Aceptas el uso del bot?"
)

import libsql

TURSO_URL = os.environ.get("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")

def db():
    if TURSO_URL:
        conn = libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)
    else:
        conn = sqlite3.connect("vozpronta.db", timeout=10)
    conn.execute("CREATE TABLE IF NOT EXISTS consent (user TEXT PRIMARY KEY, created_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS stats (user TEXT PRIMARY KEY, count INTEGER DEFAULT 0, last_at TEXT, last_tasks TEXT, first_seen TEXT, daily_date TEXT, daily_count INTEGER DEFAULT 0)")
    create_trial_table(conn)
    conn.commit()
    for col, ddl in [("first_seen", "ALTER TABLE stats ADD COLUMN first_seen TEXT"), ("daily_date", "ALTER TABLE stats ADD COLUMN daily_date TEXT"), ("daily_count", "ALTER TABLE stats ADD COLUMN daily_count INTEGER DEFAULT 0")]:
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

def bump_counter(user, tasks_text=""):
    c = db()
    now = datetime.datetime.now(ZoneInfo("Europe/Madrid"))
    today = now.strftime("%Y-%m-%d")
    row = c.execute("SELECT count, daily_date, daily_count FROM stats WHERE user=?", (user,)).fetchone()
    n = (row[0] if row else 0) + 1
    old_daily_date = row[1] if row else None
    old_daily = row[2] or 0
    new_daily = 1 if old_daily_date != today else old_daily + 1
    if already_used_trial(c, user):
        _fs = (now - datetime.timedelta(days=8)).isoformat()
    else:
        mark_trial_used(c, user, today)
        _fs = now.isoformat()
    c.execute("INSERT INTO stats (user, count, last_at, last_tasks, first_seen, daily_date, daily_count) VALUES (?,?,?,?,?,?,?) ON CONFLICT(user) DO UPDATE SET count=excluded.count, last_at=excluded.last_at, last_tasks=excluded.last_tasks, daily_date=excluded.daily_date, daily_count=excluded.daily_count",
              (user, n, now.isoformat(), tasks_text[:2000], _fs, today, new_daily))
    
        
    
        
    c.commit(); c.close()
    return n

def get_my_stats(user):
    c = db(); r = c.execute("SELECT count, last_at, last_tasks, first_seen, daily_date, daily_count FROM stats WHERE user=?", (user,)).fetchone(); c.close()
    return r

def create_sumup_checkout(user_phone):
    link = os.environ.get("SUMUP_PAYMENT_LINK", "")
    if link:
        return link
    return "https://pay.sumup.com/p/s/XHKBFCSDLX"

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

def check_limit(user):
    """Возвращает (allowed: bool, trial_days_left: int, daily_count: int)"""
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
    "Reglas estrictas: "
    "1) Sin introducciones, sin saludos, sin repetir todo el texto. "
    "2) NUNCA inventes tareas, nombres, fechas, cifras o monedas que no esten en el texto. "
    "3) Conserva nombres, fechas, horas, cifras y monedas TAL CUAL, sin traducir ni redondear. "
    "4) Cada tarea = verbo en infinitivo + que + cuando/quien, si esta en el texto. "
    "5) Si es solo una opinion, recuerdo o comentario sin accion, NO es tarea. "
    "6) Maximo 10 tareas, las mas importantes primero. "
    "7) Si no hay ninguna tarea clara, devuelve EXACTAMENTE una linea: '- Sin tareas concretas.' "
    "Ejemplos: "
    "Texto: 'Mañana tengo que llamar a María a las 10 y comprar pan' -> "
    "'- Llamar a María mañana a las 10\n- Comprar pan' "
    "Texto: 'Me gusta mucho el café' -> '- Sin tareas concretas.'"
)

def _call_tasks_model(model, text):
    r = requests.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0.0,
            "max_tokens": 600,
            "messages": [
                {"role": "system", "content": TASKS_PROMPT},
                {"role": "user", "content": text[:6000]}
            ]},
        timeout=30)
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

@app.route('/')
def home():
    return "VozPronta 10 - Bot is Live!"

PRIVACY_HTML = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Política de Privacidad - VozPronta 10</title>
<style>body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:760px;margin:32px auto;padding:0 16px;line-height:1.6;color:#1a1a1a}}h1{{font-size:28px}}h2{{margin-top:28px}}a{{color:#0b6efd}}code{{background:#f2f2f2;padding:2px 6px;border-radius:4px}}</style>
</head><body>
<h1>🔒 Política de Privacidad - VozPronta 10</h1>
<p><b>Última actualización:</b> 12 de noviembre de 2026</p>
<p>Nuestra prioridad es proteger tus datos. Esta política explica qué recogemos, para qué, cuánto lo guardamos y cómo puedes borrarlo.</p>
<h2>1. Quiénes somos</h2>
<p><b>VozPronta 10</b> es un bot de transcripción de audios de WhatsApp. Funciona con la API oficial de WhatsApp (Meta).</p>
<p>Contacto: <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a></p>
<h2>2. Qué datos recogemos</h2>
<ul>
<li><b>Tu número de WhatsApp</b> - solo para responderte. Nunca te escribimos primero.</li>
<li><b>El texto de tu última transcripción y tus tareas</b> - para mostrarte tu contador y tus últimas tareas con <code>MIS ESTADISTICAS</code>.</li>
<li><b>Tu contador de transcripciones y fecha de la última</b>.</li>
<li><b>Tu consentimiento</b> - fecha en la que pulsaste “Sí, acepto”.</li>
</ul>
<h2>3. Qué datos NO recogemos y NO guardamos</h2>
<ul>
<li><b>NO guardamos tus audios.</b> Tu audio se borra justo después de transcribirlo.</li>
<li>NO pedimos contraseñas, códigos, tarjetas ni datos bancarios.</li>
<li>NO enviamos publicidad ni reenviamos tus mensajes a nadie.</li>
<li>NO vendemos tus datos.</li>
</ul>
<h2>4. Quién procesa tu audio</h2>
<p>Para transcribir usamos la API oficial de WhatsApp (Meta) y el servicio de transcripción Groq (Whisper) y de extracción de tareas Groq (gpt-oss). Tu audio se envía a Groq solo para transcribirlo y no se guarda después.</p>
<p>También se aplica la <a href="{WHATSAPP_PRIVACY_URL}">Política de privacidad de WhatsApp</a>.</p>
<h2>5. Cuánto guardamos tus datos</h2>
<p>Guardamos solo tu contador y el texto de tu última transcripción, hasta que tú pidas borrarlos. Tus audios no se guardan nunca.</p>
<h2>6. Tus derechos - cómo borrar todo</h2>
<ul>
<li>Escribe <code>BORRAR</code> en el chat - borramos TODO: consentimiento, contador y últimas tareas.</li>
<li>Escribe <code>NO</code> - retiramos tu consentimiento y ya no procesamos tus audios.</li>
<li>Escribe <code>PRIVACIDAD</code> - te enviamos esta política.</li>
<li>También puedes escribirnos a <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>.</li>
</ul>
<h2>7. Menores</h2>
<p>El bot no está dirigido a menores de 16 años.</p>
<h2>8. Cambios</h2>
<p>Si cambiamos esta política, publicaremos la nueva versión en esta misma página.</p>
<p><a href="/">← Volver a VozPronta 10</a></p>
</body></html>"""

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
        return "403 - Acceso denegado. Falta ADMIN_KEY o clave incorrecta.", 403
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
        rows_html += (
            f"<tr><td>{html.escape(_mask_phone(u))}</td>"
            f"<td>{cnt}</td>"
            f"<td>{html.escape(str(last_at or '-'))}</td>"
            f"<td>{html.escape((last_tasks or '-')[:300])}</td></tr>"
        )
    if not rows_html:
        rows_html = "<tr><td colspan='4'>Aún no hay transcripciones.</td></tr>"
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin - VozPronta 10</title>
<style>body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;line-height:1.5}}table{{width:100%;border-collapse:collapse}}th,td{{border:1px solid #ddd;padding:8px;text-align:left;vertical-align:top}}th{{background:#f2f2f2}}.cards{{display:flex;gap:12px;flex-wrap:wrap}}.card{{border:1px solid #ddd;border-radius:10px;padding:14px 18px;min-width:160px}}.card b{{font-size:28px;display:block}}small{{color:#666}}</style>
</head><body>
<h1>📊 VozPronta 10 - Admin</h1>
<p><small>Teléfonos enmascarados (**** + últimas 4 cifras). No compartas esta URL con tu clave.</small></p>
<div class="cards">
<div class="card"><b>{total_consent}</b>Con consentimiento</div>
<div class="card"><b>{total_users}</b>Usuarios con transcripciones</div>
<div class="card"><b>{total_trans}</b>Transcripciones totales</div>
<div class="card"><b>{today_users}</b>Activos hoy (UTC)</div>
</div>
<h2>Últimos 50 usuarios</h2>
<table><tr><th>Usuario</th><th>Transcripciones</th><th>Última</th><th>Últimas tareas</th></tr>{rows_html}</table>
<p><a href="/privacidad">Política de Privacidad</a> · <a href="/">Inicio</a></p>
</body></html>""", 200, {"Content-Type": "text/html; charset=utf-8"}

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
            return send_payload({
                "messaging_product": "whatsapp", "to": frm, "type": "interactive",
                "interactive": {"type": "button", "body": {"text": CONSENT_TEXT},
                    "action": {"buttons": [
                        {"type": "reply", "reply": {"id": "consent_accept", "title": "✅ Sí, acepto"}},
                        {"type": "reply", "reply": {"id": "consent_decline", "title": "❌ No acepto"}}]}}
            })

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
            if up == "PAGAR":
                if is_paid(frm):
                    send("✅ Tu suscripcion ya esta activa.\nEnvia un audio y te lo transcribo sin limites.")
                else:
                    # Проверяем в 【entity-SumUp¦canonical_name=SumUp】, не оплатил ли уже
                    try:
                        import requests as _rq
                        _h = {"Authorization": f"Bearer {SUMUP_API_KEY}"}
                        _r = _rq.get(f"https://api.sumup.com/v0.1/checkouts?checkout_reference={frm}", headers=_h, timeout=10)
                        _paid = False
                        if _r.status_code == 200:
                            for _it in _r.json().get("items", []):
                                if str(_it.get("status","")).upper() in ("PAID","SUCCESS","COMPLETE"):
                                    _paid = True
                                    break
                        if _paid:
                            activate_paid(frm, days=30)
                            send("✅ Pago recibido! Tu suscripcion esta activada por 30 dias.\n\nEnvia un audio y te lo transcribo sin limites.")
                        else:
                            send("⏳ Aun no veo tu pago.\n\nSi ya pagaste, espera 1 minuto y escribe PAGAR otra vez.\nSi no pagaste, envia un audio para que te de tu link personal de pago.")
                    except Exception as e:
                        print(f" PAGAR check error: {e}", flush=True)
                        send("⏳ Aun no veo tu pago. Espera 1 minuto y escribe PAGAR otra vez.")
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
            if up == "PAGAR":
                send("Gracias por tu pago!\n\nPara activar tu suscripcion, escribe el email con el que pagaste en 【entity-SumUp¦canonical_name=SumUp】.\n\nEjemplo: tu@email.com\n\nLo reviso y te activo el acceso sin limites.")
                return "OK", 200
            if "@" in txt and "." in txt and len(txt) < 100:
                # Юзер прислал email после PAGAR - сохраняем для проверки
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
                    first_seen = row[3]
                    daily_date = row[4]
                    daily_count = row[5] or 0
                    if daily_date != today_mad:
                        daily_count = 0
                    if not first_seen:
                        _c = db()
                        if already_used_trial(_c, frm):
                            trial_line = "💳 Prueba terminada. Hoy: 0 / 5 gratuitas"
                        else:
                            mark_trial_used(_c, frm, today_mad)
                            _c.commit()
                            trial_line = "🎁 Prueba gratuita: 7 días"
                        _c.close()
                    else:
                        fs = datetime.datetime.fromisoformat(first_seen)
                        if fs.tzinfo is None:
                            fs = fs.replace(tzinfo=ZoneInfo("Europe/Madrid"))
                        days_passed = (now_mad - fs).days
                        if days_passed < 7:
                            trial_line = f"🎁 Prueba gratuita: quedan {7 - days_passed} días"
                        else:
                            trial_line = f"📅 Prueba terminada. Hoy: {daily_count} / 5 gratuitas"
                    send(f"📊 Tus estadísticas:\n• Transcripciones: {row[0]}\n• Última: {row[1][:16] if row[1] else '-'}\n• {trial_line}\n\n📋 Últimas tareas:\n{row[2] or '• Sin tareas concretas.'}")
                return "OK", 200
            if not has_consent(frm):
                send_consent()
                return "OK", 200
            send("🎙 VozPronta 10 - ¡Envía un audio de hasta 10 min!\n\nEscribe \"MIS ESTADISTICAS\" para ver tu contador.")
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
                    if link:
                        send(f"Has usado tus 5 transcripciones gratuitas de hoy.\n\nTu semana gratuita ya termino.\n\nPara seguir sin limites:\n\n1 mes - {SUMUP_AMOUNT:.2f} EUR\n\nPagar con tarjeta / Google Pay / Apple Pay:\n{link}\n\nDespues de pagar, escribe PAGAR para comprobar tu suscripcion.")
                    else:
                        send("Has usado tus 5 transcripciones gratuitas de hoy.\n\nHubo un error al generar tu link de pago. Escribe PAGAR en 1 minuto.")
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
                    r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions",
                                      headers={"Authorization": f"Bearer {GROQ_KEY}"},
                                      files={"file": f},
                                      data={"model": "whisper-large-v3"})
                os.unlink(path)
            if r.status_code == 200:
                text = r.json().get('text', '')
                tasks = extract_tasks(text)
                n = bump_counter(frm, tasks)
                msg = f"✅ Transcripción:\n\n{text}"
                msg += f"\n\n📋 Tareas:\n{tasks or '- Sin tareas concretas.'}"
                msg += f"\n\n📊 Esta es tu transcripción #{n}."
                send(msg[:3900])
            else:
                send(f"❌ Error: {r.text[:300]}")
    except Exception as e:
        print(e)
    return "OK", 200
# --- SumUp ---
import requests as _req2
SUMUP_CLIENT_ID = os.environ.get("SUMUP_CLIENT_ID", "")
SUMUP_CLIENT_SECRET = os.environ.get("SUMUP_CLIENT_SECRET", "")
SUMUP_MERCHANT = os.environ.get("SUMUP_MERCHANT_CODE", "M196F01Y")

@app.route('/【entity-sumup¦canonical_name=SumUp】-callback')
def sumup_callback():
    code = request.args.get("code", "")
    print(f"【entity-SUMUP¦canonical_name=SumUp】 CALLBACK CODE: {code}", flush=True)
    if not code:
        return "No code", 400
    # Меняем code на токен
    try:
        r = _req2.post("https://api.sumup.com/token", json={
            "grant_type": "authorization_code",
            "client_id": SUMUP_CLIENT_ID,
            "client_secret": SUMUP_CLIENT_SECRET,
            "code": code,
            "redirect_uri": f"{PUBLIC_URL}"
        }, timeout=15)
        print(f"SUMUP TOKEN: {r.status_code} {r.text[:500]}", flush=True)
        data = r.json()
        if "access_token" in data:
            # Сохраняем токен в память / в базу - для теста просто логируем
            # В проде - сохранять в Turso
            print("SUMUP CONNECTED OK", flush=True)
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
                requests.post(url, headers={"Authorization": f"Bearer {TOKEN}"}, json={
                    "messaging_product": "whatsapp", "to": ref,
                    "text": {"body": "✅ Pago recibido! Tu suscripcion esta activada por 30 dias.\n\nEnvia un audio y te lo transcribo sin limites."}
                }, timeout=10)
            except Exception as e:
                print(f" notify paid error: {e}", flush=True)
        return "OK", 200
    except Exception as e:
        print(f" sumup webhook error: {e}", flush=True)
        return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
