from flask import Flask, request
import requests, os, tempfile, sqlite3, datetime

app = Flask(__name__)

PHONE_ID = os.environ.get("PHONE_ID", "1345713805299738")
TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
GROQ_KEY = os.environ.get("GROQ_KEY", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "vozpronta123")
# Для ВЕЧНОГО хранения: в Render -> Disks -> Mount Path: /data, тогда DB_PATH=/data/vozpronta.db
DB_PATH = os.environ.get("DB_PATH", "/data/vozpronta.db" if os.path.isdir("/data") else "vozpronta.db")

CONSENT_TEXT = (
    "👋 ¡Hola! Soy VozPronta 10.\n\n"
    "Para usar el bot necesito tu consentimiento:\n"
    "✅ Transcribo tu audio a texto para que puedas leerlo.\n"
    "🔒 No guardo tus audios, no los comparto con nadie.\n"
    "🗑️ Puedes retirar tu consentimiento en cualquier momento escribiendo \"NO\".\n\n"
    "¿Aceptas el uso del bot?"
)

# ---------- БАЗА: вечное хранение + счётчик ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS consent (user TEXT PRIMARY KEY, created_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS stats (user TEXT PRIMARY KEY, count INTEGER DEFAULT 0, last_at TEXT, last_tasks TEXT)")
    conn.commit()
    return conn

def has_consent(user):
    c = db(); r = c.execute("SELECT 1 FROM consent WHERE user=?", (user,)).fetchone(); c.close()
    return bool(r)

def save_consent(user):
    c = db(); c.execute("INSERT OR REPLACE INTO consent VALUES (?,?)", (user, datetime.datetime.utcnow().isoformat())); c.commit(); c.close()

def remove_consent(user):
    c = db(); c.execute("DELETE FROM consent WHERE user=?", (user,)); c.commit(); c.close()

def bump_counter(user, tasks_text=""):
    c = db()
    row = c.execute("SELECT count FROM stats WHERE user=?", (user,)).fetchone()
    n = (row[0] if row else 0) + 1
    c.execute("INSERT INTO stats VALUES (?,?,?,?) ON CONFLICT(user) DO UPDATE SET count=excluded.count, last_at=excluded.last_at, last_tasks=excluded.last_tasks",
              (user, n, datetime.datetime.utcnow().isoformat(), tasks_text[:2000]))
    c.commit(); c.close()
    return n

def get_my_stats(user):
    c = db(); r = c.execute("SELECT count, last_at, last_tasks FROM stats WHERE user=?", (user,)).fetchone(); c.close()
    return r

# ---------- Выжимка задач из текста ----------
def extract_tasks(text):
    """1. Выжимка задач по пунктам через Groq LLM. Если LLM недоступен - fallback."""
    if not GROQ_KEY or not text.strip():
        return ""
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.1-8b-instant",
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": "Eres un asistente que extrae SOLO tareas / acciones concretas de un texto en español. Devuelve una lista con viñetas, una tarea por línea, empezando con '• '. Sin introducciones, sin repetir todo el texto, sin inventar tareas que no están en el texto. Si no hay ninguna tarea clara, devuelve exactamente: '• Sin tareas concretas.' Máximo 10 tareas. Conserva nombres, fechas, cifras y monedas tal cual."},
                    {"role": "user", "content": text[:6000]}
                ]},
            timeout=30)
        out = r.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        return out if out else ""
    except Exception as e:
        print(f"tasks error: {e}")
        return ""

@app.route('/')
def home():
    return "VozPronta 10 - Bot is Live!"

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
            return send_payload({"messaging_product":"whatsapp","to":frm,"text":{"body":t}})
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
                send("✅ ¡Gracias! Consentimiento aceptado.\n\n🎙️ Ahora envía un audio de hasta 10 min y te lo transcribo.")
            elif btn_id == "consent_decline":
                remove_consent(frm)
                send("❌ Entendido. No usaré el bot contigo.\nSi cambias de opinión, escribe \"Hola\" y vuelve a aceptar.")
            return "OK", 200

        if typ == "text":
            txt = m['text']['body'].strip()
            print(f"TEXT from {frm}: {txt}", flush=True)
            up = txt.upper()
            if up in ("NO", "NO ACEPTO", "STOP", "BAJA"):
                remove_consent(frm)
                send("❌ Consentimiento retirado. Ya no procesaré tus audios. Escribe \"Hola\" para volver a aceptar.")
                return "OK", 200
            # 3. Счётчик - команда клиента
            if up in ("MIS ESTADISTICAS", "MIS ESTADÍSTICAS", "ESTADISTICAS", "ESTADÍSTICAS", "MI CONTADOR", "STATS"):
                row = get_my_stats(frm)
                if not row:
                    send("📊 Aún no tienes transcripciones.\nEnvía un audio y aquí aparecerá tu contador.")
                else:
                    send(f"📊 Tus estadísticas:\n• Transcripciones: {row[0]}\n• Última: {row[1][:16] if row[1] else '-'}\n\n📋 Últimas tareas:\n{row[2] or '• Sin tareas concretas.'}")
                return "OK", 200
            if not has_consent(frm):
                send_consent()
                return "OK", 200
            send("🎙️ VozPronta 10 - ¡Envía un audio de hasta 10 min!\n\nEscribe \"MIS ESTADISTICAS\" para ver tu contador.")
            return "OK", 200

        if typ in ("audio","voice"):
            if not has_consent(frm):
                send_consent()
                send("⚠️ Primero pulsa \"✅ Sí, acepto\" arriba y vuelve a enviar el audio.")
                return "OK", 200
            aid = m['audio']['id']
            info = requests.get(f"https://graph.facebook.com/v19.0/{aid}", headers={"Authorization": f"Bearer {TOKEN}"}).json()
            if 'url' not in info:
                send("❌ Error al descargar el audio")
                return "OK", 200
            aud = requests.get(info['url'], headers={"Authorization": f"Bearer {TOKEN}"}).content
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf:
tf.write(aud); path = tf.name
            with open(path, 'rb') as f:
                r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions",
                                  headers={"Authorization": f"Bearer {GROQ_KEY}"},
                                  files={"file": f},
                                  data={"model":"whisper-large-v3","language":"es"})
            os.unlink(path)
            if r.status_code == 200:
                text = r.json().get('text','')
                # 1. Выжимка задач
                tasks = extract_tasks(text)
                # 3. Счётчик
                n = bump_counter(frm, tasks)
                msg = f"✅ Transcripción:\n\n{text}"
                if tasks:
                    msg += f"\n\n📋 Tareas:\n{tasks}"
                msg += f"\n\n📊 Esta es tu transcripción #{n}."
                send(msg[:3900])
            else:
                send(f"❌ Error: {r.text[:300]}")
    except Exception as e:
        print(e)
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
