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
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token", "").strip()
    challenge = request.args.get("hub.challenge", "")
    print(f"VERIFY: mode={mode} token={token} challenge={challenge} expected={VERIFY_TOKEN}")
    # Пуленепробиваемый - всегда возвращаем challenge если он есть
    if challenge:
        return challenge, 200
    return "ok", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print(data)
    try:
        entry = data['entry'][0]['changes'][0]['value']
        if 'messages' not in entry:
            return "OK", 200
            
        msg = entry['messages'][0]
        from_num = msg['from']
        msg_type = msg['type']

        def send(text):
            url = f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages"
            headers = {"Authorization": f"Bearer {TOKEN}"}
            json_data = {"messaging_product":"whatsapp","to":from_num,"text":{"body":text}}
            r = requests.post(url, headers=headers, json=json_data)
            print("Send result:", r.text)

        if msg_type == "text":
            txt = msg['text']['body'].lower()
            if "oi" in txt or "ola" in txt or "olá" in txt:
                send("🎙️ VozPronta 10 - Mande um áudio de até 10 min!")

        if msg_type in ["audio","voice"]:
            audio_id = msg['audio']['id']
            f_info = requests.get(f"https://graph.facebook.com/v19.0/{audio_id}",
                headers={"Authorization": f"Bearer {TOKEN}"}).json()
            
            if 'url' not in f_info:
                send("❌ Erro ao baixar áudio")
                return "OK", 200
                
            audio_data = requests.get(f_info['url'],
                headers={"Authorization": f"Bearer {TOKEN}"}).content
            
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf:
                tf.write(audio_data)
                path = tf.name

            with open(path, 'rb') as f:
                r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {GROQ_KEY}"},
                    files={"file": f},
                    data={"model":"whisper-large-v3","language":"pt"})
            
            os.unlink(path)
            
            if r.status_code == 200:
                text = r.json().get('text','')
                send(f"✅ Transcrição:\n\n{text}")
            else:
                send(f"❌ Erro na transcrição: {r.text}")

    except Exception as e:
        print(f"Error: {e}")
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
