from flask import Flask, request
import requests, os, tempfile
app = Flask(__name__)
PHONE_ID = os.environ.get("PHONE_ID", "1345713805299738")
TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
GROQ_KEY = os.environ.get("GROQ_KEY", "")
@app.route('/')
def home():
    return "VozPronta 10 - Bot is Live!"
@app.route('/webhook', methods=['GET'])
def verify():
    print("VERIFY:", dict(request.args))
    token = request.args.get("hub.verify_token","").strip()
    challenge = request.args.get("hub.challenge","")
    if token == "vozpronta123":
        return challenge, 200
    env = os.environ.get("VERIFY_TOKEN","").strip()
    if env and token == env:
        return challenge, 200
    return challenge or "ok", 200
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        print(data)
        entry = data['entry'][0]['changes'][0]['value']
        if 'messages' not in entry:
            return "OK", 200
        msg = entry['messages'][0]
        from_num = msg['from']
        mtype = msg['type']
        def send(t):
            url = f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages"
            headers = {"Authorization": f"Bearer {TOKEN}"}
            requests.post(url, headers=headers, json={"messaging_product":"whatsapp","to":from_num,"text":{"body":t}})
        if mtype == "text":
            low = msg['text']['body'].lower()
            if any(x in low for x in ["oi","ola","olá"]):
                send("🎙️ VozPronta 10 - Mande um áudio de até 10 min!")
        if mtype in ["audio","voice"]:
            aid = msg['audio']['id']
            finfo = requests.get(f"https://graph.facebook.com/v19.0/{aid}", headers={"Authorization": f"Bearer {TOKEN}"}).json()
            if 'url' not in finfo:
                send("❌ Erro ao baixar")
                return "OK", 200
            adata = requests.get(finfo['url'], headers={"Authorization": f"Bearer {TOKEN}"}).content
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf:
                tf.write(adata)
                p = tf.name
            with open(p,'rb') as f:
                r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {GROQ_KEY}"},
                    files={"file": f}, data={"model":"whisper-large-v3","language":"pt"})
            os.unlink(p)
            if r.status_code == 200:
                send(f"✅ Transcrição:\n\n{r.json().get('text','')}")
            else:
                send(f"❌ Erro: {r.text[:200]}")
    except Exception as e:
        print(f"ERR {e}")
    return "OK", 200
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
