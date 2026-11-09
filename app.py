from flask import Flask, request
import requests, os, tempfile

app = Flask(__name__)

PHONE_ID = os.environ.get("PHONE_ID", "1345713805299738")
TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
GROQ_KEY = os.environ.get("GROQ_KEY", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "vozpronta123")

@app.route('/')
def home():
    return "VozPronta 10 - Bot is Live!"

@app.route('/webhook', methods=['GET'])
def verify():
    # Meta присылает: ?hub.mode=subscribe&hub.verify_token=vozpronta123&hub.challenge=12345
    # Мы должны просто вернуть hub.challenge как есть
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
        def send(t):
            url = f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages"
            h = {"Authorization": f"Bearer {TOKEN}"}
            p = {"messaging_product":"whatsapp","to":frm,"text":{"body":t}}
            r = requests.post(url, headers=h, json=p)
            print(f"SEND to {frm}: {r.status_code} {r.text[:500]}", flush=True)
        if typ == "text":
            txt = m['text']['body']
            print(f"TEXT from {frm}: {txt}", flush=True)
            send("🎙️ VozPronta 10 - ¡Envía un audio de hasta 10 min!")
        if typ in ("audio","voice"):
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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
