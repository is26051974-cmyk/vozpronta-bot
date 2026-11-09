from flask import Flask, request
import requests, os, tempfile, json

app = Flask(__name__)

PHONE_ID = os.environ.get("PHONE_ID", "1345713805299738")
TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
GROQ_KEY = os.environ.get("GROQ_KEY", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "vozpronta123")
CONSENT_FILE = os.environ.get("CONSENT_FILE", "/tmp/consent.json")

CONSENT_TEXT = (
    "👋 ¡Hola! Soy VozPronta 10.\n\n"
    "Para usar el bot necesito tu consentimiento:\n"
    "✅ Transcribo tu audio a texto para que puedas leerlo.\n"
    "🔒 No guardo tus audios, no los comparto con nadie.\n"
    "🗑️ Puedes retirar tu consentimiento en cualquier momento escribiendo \"NO\".\n\n"
    "¿Aceptas el uso del bot?"
)

def load_consent():
    try:
        with open(CONSENT_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
            return set(d) if isinstance(d, list) else set()
    except Exception:
        return set()

def save_consent(user):
    s = load_consent()
    s.add(user)
    try:
        with open(CONSENT_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(s), f)
    except Exception as e:
        print(f"consent save error: {e}")

def remove_consent(user):
    s = load_consent()
    s.discard(user)
    try:
        with open(CONSENT_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(s), f)
    except Exception as e:
        print(f"consent remove error: {e}")

@app.route('/')
def home():
    return "VozPronta 10 - Bot is Live!"

@app.route('/webhook', methods=['GET'])
def verify():
    challenge = request.args.get("hub.challenge")
    token = request.args.get("hub.verify_token")
    print(f"VERIFY: token={token} challenge={challenge}")
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
            # Кнопки как в ViraTexto: Aceptar / No acepto
            return send_payload({
                "messaging_product": "whatsapp",
                "to": frm,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": CONSENT_TEXT},
                    "action": {"buttons": [
                        {"type": "reply", "reply": {"id": "consent_accept", "title": "✅ Sí, acepto"}},
                        {"type": "reply", "reply": {"id": "consent_decline", "title": "❌ No acepto"}}
                    ]}
                }
            })

        # --- Нажатие кнопки ---
        if typ == "interactive":
            btn_id = m.get('interactive', {}).get('button_reply', {}).get('id', '')
            print(f"BUTTON from {frm}: {btn_id}", flush=True)
            if btn_id == "consent_accept":
                save_consent(frm)
                send("✅ ¡Gracias! Consentimiento aceptado.\n\n🎙️ Ahora envía un audio de hasta 10 min y te lo transcribo.")
            elif btn_id == "consent_decline":
                remove_consent(frm)
                send("❌ Entendido. No usaré el bot contigo.\nSi cambias de opinión, escribe \"Hola\" y vuelve a aceptar.")
            return "OK", 200
# --- Отзыв согласия текстом ---
        if typ == "text":
            txt = m['text']['body'].strip()
            print(f"TEXT from {frm}: {txt}", flush=True)
            if txt.upper() in ("NO", "NO ACEPTO", "STOP", "BAJA"):
                remove_consent(frm)
                send("❌ Consentimiento retirado. Ya no procesaré tus audios. Escribe \"Hola\" para volver a aceptar.")
                return "OK", 200
            if frm not in load_consent():
                send_consent()
                return "OK", 200
            send("🎙️ VozPronta 10 - ¡Envía un audio de hasta 10 min!")
            return "OK", 200

        if typ in ("audio","voice"):
            if frm not in load_consent():
                # Как в ViraTexto: не транскрибируем без согласия, сначала кнопка
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
                tf.write(aud)
                path = tf.name
            with open(path, 'rb') as f:
                r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions",
                                  headers={"Authorization": f"Bearer {GROQ_KEY}"},
                                  files={"file": f},
                                  data={"model":"whisper-large-v3","language":"es"})
            os.unlink(path)
            if r.status_code == 200:
                send(f"✅ Transcripción:\n\n{r.json().get('text','')}")
            else:
                send(f"❌ Error: {r.text[:300]}")
    except Exception as e:
        print(e)
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
