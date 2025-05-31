from flask import Flask, request, jsonify
import requests # For sending requests to Discord webhook
import json
import os
from datetime import datetime, timezone # For converting timestamp
import re # For parsing ancillaryData
from threading import Thread # Import Thread

# --- Configuration ---
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "YOUR_DISCORD_WEBHOOK_URL_HERE")
FLASK_PORT = int(os.environ.get("FLASK_PORT", 5001)) # Used by Flask's dev server, not Gunicorn directly on Railway

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Helper Functions ---
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
    # Regex to find "title: " (case-insensitive) and capture until a common delimiter or end of string/line
    match = re.search(r"title:\s*(.*?)(?:,\s*description:|, desc:|resolution_criteria:|\n|$)", ancillary_data_str, re.IGNORECASE | re.DOTALL)
    if match:
        title = match.group(1).strip()
        # Clean up potential non-printable characters and excessive length
        title = title.replace('\u0000', '').strip()
        if len(title) > 250: # Max title length for Discord embeds is 256
            title = title[:250] + "..."
        return title
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
        # Set a timeout for the request to Discord (e.g., 10 seconds)
        response = requests.post(DISCORD_WEBHOOK_URL, json=data, timeout=10)
        response.raise_for_status()
        print(f"Message sent to Discord (Status: {response.status_code})")
        return True
    except requests.exceptions.Timeout:
        print(f"Error sending message to Discord: Timeout after 10 seconds.")
        return False
    except requests.exceptions.RequestException as e:
        print(f"Error sending message to Discord: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Discord Response Content: {e.response.text}")
        return False

# --- Function to Handle Processing in Background ---
def process_webhook_payload(payload):
    """
    Parses the payload and sends a message to Discord.
    Run in a background thread.
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
                                else:
                                    print(f"Could not extract title from ancillary data: {ancillary_data_str[:100]}...")
                            else:
                                print(f"Failed to decode ancillary data hex: {ancillary_data_hex[:100]}...")
                        else:
                            print("Ancillary data is empty or '0x'.")

                        raw_proposed_price = event_params.get("proposedPrice")
                        disputed_answer = str(raw_proposed_price)
                        price_value_str = str(raw_proposed_price) # Ensure it's a string for comparison
                        if price_value_str == "0":
                            disputed_answer = "p1 (e.g., NO)"
                        elif price_value_str == "1000000000000000000": # 1 * 10^18
                            disputed_answer = "p2 (e.g., YES)"
                        elif price_value_str == "500000000000000000":  # 0.5 * 10^18
                            disputed_answer = "p3 (e.g., 0.5/INVALID)"
                        
                        disputer_address = event_params.get("disputer", "N/A")
                        requester_address = event_params.get("requester", "N/A")
                        proposer_address = event_params.get("proposer", "N/A")
                        event_timestamp_unix = event_params.get("timestamp")

                        tx_hash = activity_item.get("hash", "N/A")
                        block_num_hex = activity_item.get("blockNum") # This comes from Alchemy's payload structure
                        block_num = int(block_num_hex, 16) if block_num_hex else "N/A"
                        
                        network = payload.get("event", {}).get("network", "ETH_MAINNET").upper()
                        etherscan_base = "https://polygonscan.com" if "POLYGON" in network or "MATIC" in network else "https://etherscan.io"
                        tx_link = f"{etherscan_base}/tx/{tx_hash}" if tx_hash != "N/A" else "N/A"
                        disputer_link = f"{etherscan_base}/address/{disputer_address}" if disputer_address != "N/A" else "N/A"

                        event_time_str = "N/A"
                        if event_timestamp_unix:
                            try:
                                # Ensure timestamp is treated as an integer
                                event_time_str = datetime.fromtimestamp(int(event_timestamp_unix), timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
                            except (ValueError, TypeError) as ts_err:
                                print(f"Error converting timestamp {event_timestamp_unix}: {ts_err}")
                                pass

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
                            "timestamp": payload.get("createdAt") # Use Alchemy's webhook creation timestamp
                        }
                        send_to_discord(embeds=[embed])
                    else:
                        print(f"Received event '{event_name}', but not processing as it's not '{TARGET_DISPUTE_EVENT_NAME}'.")
                else:
                    # This case might happen if Alchemy couldn't decode the log (e.g., missing ABI for the event)
                    print("No 'decoded' data found in log_data. Raw log:")
                    print(json.dumps(log_data, indent=2))
        else:
            print("Payload structure not as expected (missing 'event' or 'activity'). Full payload:")
            print(json.dumps(payload, indent=2))
            send_to_discord(message_content=f"Received an unhandled webhook structure from Alchemy:\n```json\n{json.dumps(payload, indent=2)}\n```")

    except Exception as e:
        print(f"Error during background processing of webhook payload: {e}")
        # Optionally send an error message to a different Discord webhook for debugging
        # send_to_discord(message_content=f":x: Error processing payload in background: {e}")

# --- Updated Webhook Endpoint ---
@app.route('/alchemy-webhook', methods=['POST'])
def alchemy_webhook_receiver():
    """
    Receives webhook from Alchemy, acknowledges it immediately,
    and starts the actual processing in a background thread.
    """
    try:
        payload = request.json
        if not payload:
            print("Received empty or invalid payload at /alchemy-webhook.")
            return jsonify({"status": "error", "message": "Empty or invalid payload"}), 400

        print(f"--- Received webhook from Alchemy. Starting background processing. ---")
        
        thread = Thread(target=process_webhook_payload, args=(payload,))
        thread.start()

        return jsonify({"status": "success", "message": "Webhook received and processing initiated"}), 200
    except Exception as e:
        # This handles errors in receiving/parsing the initial request, before threading
        print(f"Error in alchemy_webhook_receiver (before threading): {e}")
        return jsonify({"status": "error", "message": "Error handling initial request"}), 500


# --- Health Check and Main Execution ---
@app.route('/', methods=['GET'])
def health_check():
    return "Webhook receiver is alive!", 200

if __name__ == '__main__':
    if "YOUR_DISCORD_WEBHOOK_URL_HERE" in DISCORD_WEBHOOK_URL:
        print("CRITICAL: Please set your DISCORD_WEBHOOK_URL in the script or as an environment variable.")
        # For Railway, this check might not be critical if env var is set on the platform
        # For local dev, it's important.
        # exit(1) # Commenting out exit for Railway deployments where env var is expected

    # When run directly (python app.py), use Flask's dev server
    # Gunicorn will be used by Railway based on Procfile or start command
    print(f"Starting Flask development server on http://0.0.0.0:{FLASK_PORT}...")
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=False) # Set debug=False for production/Gunicorn