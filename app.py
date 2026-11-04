from flask import Flask, request

app = Flask(__name__)

VERIFY_TOKEN = "vozpronta123"

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
    print("Webhook received:", request.get_json())
    return "OK", 200

if name == "main":
    app.run(host="0.0.0.0", port=10000)
