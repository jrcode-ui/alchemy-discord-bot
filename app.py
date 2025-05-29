from flask import Flask, request, jsonify
import requests # For sending requests to Discord webhook
import json
import os

# --- Configuration - PLEASE REPLACE WITH YOUR ACTUAL VALUES ---

# Discord Webhook URL (get this from your Discord server settings)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "YOUR_DISCORD_WEBHOOK_URL_HERE")

# Port for the Flask app to run on
FLASK_PORT = int(os.environ.get("FLASK_PORT", 5001))

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Helper Function to Send Message to Discord ---
def send_to_discord(message_content=None, embeds=None):
    """
    Sends a message or embeds to the configured Discord webhook.
    """
    if not DISCORD_WEBHOOK_URL or "YOUR_DISCORD_WEBHOOK_URL_HERE" in DISCORD_WEBHOOK_URL:
        print("Error: DISCORD_WEBHOOK_URL is not configured.")
        return False

    data = {}
    if message_content:
        data["content"] = message_content
    if embeds:
        data["embeds"] = embeds if isinstance(embeds, list) else [embeds]

    if not data:
        print("No content or embeds to send to Discord.")
        return False

    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=data)
        response.raise_for_status()
        print(f"Message sent to Discord (Status: {response.status_code})")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error sending message to Discord: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Discord Response Content: {e.response.text}")
        return False

# --- Webhook Endpoint for Alchemy Notifications ---
@app.route('/alchemy-webhook', methods=['POST'])
def alchemy_webhook_receiver():
    """
    Receives webhook notifications from Alchemy.
    """
    try:
        payload = request.json
        if not payload:
            print("Received empty payload.")
            return jsonify({"status": "error", "message": "Empty payload"}), 400

        print("\n--- Received Alchemy Webhook Payload ---")
        print(json.dumps(payload, indent=2))

        if payload.get("event") and payload["event"].get("activity"):
            for activity_item in payload["event"]["activity"]:
                log_data = activity_item.get("log")
                if log_data and log_data.get("decoded"):
                    decoded_event = log_data["decoded"]
                    event_name = decoded_event.get("name")
                    params = decoded_event.get("params", [])

                    TARGET_DISPUTE_EVENT_NAME = "MarketDisputed" # Or whatever your event is named

                    if event_name == TARGET_DISPUTE_EVENT_NAME:
                        print(f"Processing '{event_name}' event...")

                        event_params = {p["name"]: p["value"] for p in params}

                        market_title_or_id = event_params.get("marketTitle", event_params.get("title", event_params.get("marketId", "N/A")))
                        proposal_disputed = event_params.get("proposalDisputed", event_params.get("disputedOutcome", "N/A"))
                        disputer_address = event_params.get("disputer", event_params.get("disputerAddress", "N/A"))

                        tx_hash = activity_item.get("hash", "N/A")
                        block_num_hex = activity_item.get("blockNum")
                        block_num = int(block_num_hex, 16) if block_num_hex else "N/A"
                        network = payload.get("event", {}).get("network", "ETH_MAINNET").upper()

                        etherscan_base = "https://etherscan.io"
                        if "POLYGON" in network or "MATIC" in network :
                             etherscan_base = "https://polygonscan.com"
                        # Add other networks as needed

                        tx_link = f"{etherscan_base}/tx/{tx_hash}" if tx_hash != "N/A" else "N/A"
                        disputer_link = f"{etherscan_base}/address/{disputer_address}" if disputer_address != "N/A" else "N/A"

                        # --- Format Message for Discord ---
                        embed_title = f"❌ Market Disputed ❌"
                        # The main title of the market/event
                        embed_description = f"**Title:** {market_title_or_id}\n"

                        embed = {
                            "title": embed_title,
                            "description": embed_description,
                            "color": 0xFF0000,  # Red
                            "fields": [
                                {"name": "Proposal Disputed", "value": str(proposal_disputed), "inline": True},
                                {"name": "Disputer", "value": f"[{str(disputer_address)}]({disputer_link})", "inline": True},
                                {"name": "Transaction", "value": f"[{tx_hash[:12]}...]({tx_link})", "inline": False},
                            ],
                            "footer": {"text": f"Block: {block_num} | Network: {network}"},
                            "timestamp": payload.get("createdAt")
                        }

                        send_to_discord(embeds=[embed])
                    else:
                        print(f"Received event '{event_name}', but not processing as it's not '{TARGET_DISPUTE_EVENT_NAME}'.")
                else:
                    print("No decoded log data found in activity item.")
        else:
            print("Payload structure not as expected (missing 'event' or 'activity').")
            send_to_discord(message_content=f"Received an unhandled webhook from Alchemy:\n```json\n{json.dumps(payload, indent=2)}\n```")

        return jsonify({"status": "success", "message": "Webhook received"}), 200

    except Exception as e:
        print(f"Error processing webhook: {e}")
        send_to_discord(message_content=f":x: Error processing Alchemy webhook: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/', methods=['GET'])
def health_check():
    return "Webhook receiver is alive!", 200

if __name__ == '__main__':
    if "YOUR_DISCORD_WEBHOOK_URL_HERE" in DISCORD_WEBHOOK_URL:
        print("CRITICAL: Please set your DISCORD_WEBHOOK_URL in the script or as an environment variable.")
        exit(1)
    print(f"Starting Flask server on port {FLASK_PORT}...")
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=True)