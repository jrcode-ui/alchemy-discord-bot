from flask import Flask, request, jsonify
import requests # For sending requests to Discord webhook
import json
import os
from datetime import datetime, timezone # For converting timestamp
import re # For parsing ancillaryData
from threading import Thread # Import Thread

# --- Configuration ---
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "YOUR_DISCORD_WEBHOOK_URL_HERE")
FLASK_PORT = int(os.environ.get("FLASK_PORT", 5001))

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Helper Functions (No changes here) ---
def hex_to_string(hex_str):
    if hex_str.startswith("0x"):
        hex_str = hex_str[2:]
    try:
        byte_array = bytes.fromhex(hex_str)
        return byte_array.decode('utf-8', errors='replace')
    except Exception as e:
        print(f"Error decoding hex string {hex_str}: {e}")
        return None

def extract_title_from_ancillary(ancillary_data_str):
    if not ancillary_data_str:
        return None
    match = re.search(r"title:\s*(.*?)(?:,\s*description:|, desc:|resolution_criteria:|\n|$)", ancillary_data_str, re.IGNORECASE | re.DOTALL)
    if match:
        title = match.group(1).strip()
        return title.replace('\u0000', '').strip()
    return None

def send_to_discord(message_content=None, embeds=None):
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

# --- New Function to Handle Processing ---
def process_webhook_payload(payload):
    """
    This function contains all the logic to parse the payload and send a message to Discord.
    It will be run in a background thread so it doesn't block the response to Alchemy.
    """
    print("--- Background processing started ---")
    try:
        if payload.get("event") and payload["event"].get("activity"):
            for activity_item in payload["event"]["activity"]:
                log_data = activity_item.get("log")
                if log_data and log_data.get("decoded"):
                    decoded_event = log_data["decoded"]
                    event_name = decoded_event.get("name")
                    params = decoded_event.get("params", [])

                    TARGET_DISPUTE_EVENT_NAME = "DisputePrice"

                    if event_name == TARGET_DISPUTE_EVENT_NAME:
                        print(f"Processing '{event_name}' event in background...")

                        event_params = {p["name"]: p["value"] for p in params}
                        market_identifier_hex = event_params.get("identifier", "N/A")
                        ancillary_data_hex = event_params.get("ancillaryData", "")

                        human_readable_title = "N/A"
                        if ancillary_data_hex and ancillary_data_hex != "0x":
                            ancillary_data_str = hex_to_string(ancillary_data_hex)
                            if ancillary_data_str:
                                extracted_title = extract_title_from_ancillary(ancillary_data_str)
                                if extracted_title:
                                    human_readable_title = extracted_title

                        raw_proposed_price = event_params.get("proposedPrice")
                        disputed_answer = str(raw_proposed_price)
                        price_value_str = str(raw_proposed_price)
                        if price_value_str == "0":
                            disputed_answer = "p1 (e.g., NO)"
                        elif price_value_str == "1000000000000000000":
                            disputed_answer = "p2 (e.g., YES)"
                        elif price_value_str == "500000000000000000":
                            disputed_answer = "p3 (e.g., 0.5/INVALID)"
                        
                        disputer_address = event_params.get("disputer", "N/A")
                        requester_address = event_params.get("requester", "N/A")
                        proposer_address = event_params.get("proposer", "N/A")
                        event_timestamp_unix = event_params.get("timestamp")

                        tx_hash = activity_item.get("hash", "N/A")
                        block_num_hex = activity_item.get("blockNum")
                        block_num = int(block_num_hex, 16) if block_num_hex else "N/A"
                        network = payload.get("event", {}).get("network", "ETH_MAINNET").upper()
                        etherscan_base = "https://polygonscan.com" if "POLYGON" in network or "MATIC" in network else "https://etherscan.io"
                        tx_link = f"{etherscan_base}/tx/{tx_hash}" if tx_hash != "N/A" else "N/A"
                        disputer_link = f"{etherscan_base}/address/{disputer_address}" if disputer_address != "N/A" else "N/A"

                        event_time_str = "N/A"
                        if event_timestamp_unix:
                            try:
                                event_time_str = datetime.fromtimestamp(int(event_timestamp_unix), timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
                            except (ValueError, TypeError): pass

                        display_title = human_readable_title if human_readable_title != "N/A" else f"Market Identifier: `{market_identifier_hex}`"
                        
                        embed = {
                            "title": "❌ Price Disputed ❌",
                            "description": f"**Title/Market:** {display_title}\n",
                            "color": 0xFF0000,
                            "fields": [
                                {"name": "Disputed Outcome", "value": str(disputed_answer), "inline": True},
                                {"name": "Disputer", "value": f"[{str(disputer_address)}]({disputer_link})", "inline": True},
                                {"name": "Requester", "value": str(requester_address), "inline": True},
                                {"name": "Proposer", "value": str(proposer_address), "inline": True},
                                {"name": "Event Timestamp", "value": event_time_str, "inline": False},
                                {"name": "Transaction", "value": f"[{tx_hash[:12]}...]({tx_link})", "inline": False},
                            ],
                            "footer": {"text": f"Block: {block_num} | Network: {network}"},
                            "timestamp": payload.get("createdAt")
                        }
                        send_to_discord(embeds=[embed])
    except Exception as e:
        print(f"Error during background processing: {e}")
        # Optionally send an error message to a different Discord webhook for debugging
        # send_to_discord(message_content=f":x: Error processing payload in background: {e}")

# --- Updated Webhook Endpoint ---
@app.route('/alchemy-webhook', methods=['POST'])
def alchemy_webhook_receiver():
    """
    Receives webhook from Alchemy, acknowledges it immediately,
    and starts the actual processing in a background thread.
    """
    payload = request.json
    if not payload:
        print("Received empty or invalid payload.")
        return jsonify({"status": "error", "message": "Empty or invalid payload"}), 400

    print(f"--- Received webhook from Alchemy. Starting background processing. ---")
    
    # Create and start a new thread to process the payload
    # The 'args' tuple must have a comma even for one item
    thread = Thread(target=process_webhook_payload, args=(payload,))
    thread.start()

    # Immediately return a success response to Alchemy so it doesn't time out
    return jsonify({"status": "success", "message": "Webhook received and processing initiated"}), 200

# --- Health Check and Main Execution (No changes here) ---
@app.route('/', methods=['GET'])
def health_check():
    return "Webhook receiver is alive!", 200

if __name__ == '__main__':
    if "YOUR_DISCORD_WEBHOOK_URL_HERE" in DISCORD_WEBHOOK_URL:
        print("CRITICAL: Please set your DISCORD_WEBHOOK_URL in the script or as an environment variable.")
        exit(1)
    print(f"Starting Flask server on port {FLASK_PORT}...")
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=True)