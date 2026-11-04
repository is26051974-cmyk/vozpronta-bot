from flask import Flask, request
import requests
import os

app = Flask(name)

VERIFY_TOKEN = "vozpronta123"

# ТВОИ ТОКЕНЫ - вставь сюда если есть, или оставим из переменных Render
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "ВСТАВЬ_СЮДА_ТОКЕН_ЕСЛИ_НЕТ_В_RENDER")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "")

@app.route("/")
def home():
    return "Vozpronta bot is Live!"

@app.route("/webhook", methods=["GET"])
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    else:
        return "Verification failed", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    try:
        if data:
            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    messages = value.get("messages", [])
                    for msg in messages:
                        from_number = msg.get("from")
                        text = msg.get("text", {}).get("body", "").lower()
                        
                        if from_number:
                            send_message(from_number, "Oi! Vozpronta te escuta! 🚀 Digite: menu")
    except Exception as e:
        print(f"Error: {e}")
    
    return "OK", 200

def send_message(to, text):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        print("No token/id set")
        return
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    requests.post(url, headers=headers, json=payload)

if name == "main":
    app.run(host="0.0.0.0", port=10000)
