from flask import Flask, request
import requests, os, tempfile

app = Flask(__name__)

PHONE_ID = "1345713805299738"
TOKEN = "СЮДА_ВСТАВЬ_EAA_ТОКЕН_ИЗ_FACEBOOK"
GROQ_KEY = "СЮДА_ВСТАВЬ_gsk_КЛЮЧ"

@app.route('/webhook', methods=['GET'])
def verify():
    # для верификации в 【entity-Meta¦canonical_name=Meta】
    if request.args.get("hub.verify_token") == "vozpronta123":
        return request.args.get("hub.challenge")
    return "ok"

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    try:
        msg = data['entry'][0]['changes'][0]['value']['messages'][0]
        from_num = msg['from']
        msg_type = msg['type']

        def send(text):
            requests.post(f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={"messaging_product":"whatsapp","to":from_num,"text":{"body":text}})

        if msg_type == "text":
            txt = msg['text']['body'].lower()
            if "oi" in txt or "ola" in txt or "olá" in txt:
                send("🎙️ VozPronta 10 - Mande um áudio de até 10 min!")

        if msg_type in ["audio","voice"]:
            audio_id = msg['audio']['id']
            # получаем ссылку
            f_info = requests.get(f"https://graph.facebook.com/v19.0/{audio_id}",
                headers={"Authorization": f"Bearer {TOKEN}"}).json()
            
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
            
            text = r.json()['text']
            send(f"✅ Transcrição:\n\n{text}")

    except Exception as e:
        print(e)
    return "OK", 200

if __name__ == "__main__":
    app.run(port=10000)