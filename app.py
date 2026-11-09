from flask import Flask, request
import requests, os, tempfile

app = Flask(__name__)

PHONE_ID = os.environ.get("PHONE_ID", "1345713805299738")
TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
GROQ_KEY = os.environ.get("GROQ_KEY", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "vozpronta123")

@app.route('/')
def home():
    return "VozPronta 10 - Bot is Live! Final"

@app.route('/webhook', methods=['GET'])
def verify():
    # FINAL BULLETPROOF - Facebook требует вернуть hub.challenge как есть
    challenge = request.args.get("hub.challenge")
    if challenge is not None:
        return challenge, 200
    return "ok", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True) or {}
        entry = data.get('entry', [{}])[0].get('changes', [{}])[0].get('value', {})
        if 'messages' not in entry:
            return "OK", 200
        msg = entry['messages'][0]
        from_num = msg['from']
        mtype = msg['type']

        def send(text):
            url = f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages"
            headers = {"Authorization": f"Bearer {TOKEN}"}
            payload = {"messaging_product":"whatsapp","to":from_num,"text":{"body":text}}
            requests.post(url, headers=headers, json=payload)

        if mtype == "text":
            txt = msg['text']['body'].lower()
            if any(x in txt for x in ["oi","ola","olá","oie"]):
                send("🎙️ VozPronta 10 - Mande um áudio de até 10 min!")

        if mtype in ("audio","voice"):
            audio_id = msg['audio']['id']
            info = requests.get(f"https://graph.facebook.com/v19.0/{audio_id}",
                                headers={"Authorization": f"Bearer {TOKEN}"}).json()
            if 'url' not in info:
                send("❌ Erro ao baixar áudio")
                return "OK", 200
            aud = requests.get(info['url'], headers={"Authorization": f"Bearer {TOKEN}"}).content
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf:
                tf.write(aud)
                path = tf.name
            with open(path, 'rb') as f:
                r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions",
                                  headers={"Authorization": f"Bearer {GROQ_KEY}"},
                                  files={"file": f},
                                  data={"model":"whisper-large-v3","language":"pt"})
            os.unlink(path)
            if r.status_code == 200:
                send(f"✅ Transcrição:\n\n{r.json().get('text','')}")
            else:
                send(f"❌ Erro: {r.text[:500]}")
    except Exception as e:
        print(f"Error: {e}")
    return "OK", 200



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
