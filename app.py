from flask import Flask, request, jsonify
import requests
import json
import os
from datetime import datetime, timezone
import re
from threading import Thread
import time
from queue import Queue

# --- Configuration ---
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "YOUR_DISCORD_WEBHOOK_URL_HERE")
FLASK_PORT = int(os.environ.get("FLASK_PORT", 5001))

# --- Application Setup ---
app = Flask(__name__)
# Create a thread-safe queue to hold incoming webhook payloads
webhook_queue = Queue()

# --- Helper Functions ---
def log_timestamp(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] {message}")

def hex_to_string(hex_str):
    if hex_str.startswith("0x"):
        hex_str = hex_str[2:]
    try:
        return bytes.fromhex(hex_str).decode('utf-8', errors='replace')
    except Exception as e:
        log_timestamp(f"Error decoding hex: {e}")
        return None

def extract_title_from_ancillary(ancillary_data_str):
    if not ancillary_data_str: return None
    match = re.search(r"title:\s*(.*?)(?:,\s*description:|, desc:|resolution_criteria:|\n|$)", ancillary_data_str, re.IGNORECASE | re.DOTALL)
    if match:
        title = match.group(1).strip().replace('\u0000', '').strip()
        return title[:250] + "..." if len(title) > 250 else title
    return None

def send_to_discord(embeds=None):
    if not DISCORD_WEBHOOK_URL or "YOUR_DISCORD_WEBHOOK_URL_HERE" in DISCORD_WEBHOOK_URL:
        log_timestamp("Error: DISCORD_WEBHOOK_URL not configured.")
        return
    if not embeds: return

    try:
        log_timestamp("Posting embed to Discord...")
        response = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": embeds}, timeout=10)
        response.raise_for_status()
        log_timestamp(f"Discord POST successful (Status: {response.status_code}).")
    except requests.exceptions.Timeout:
        log_timestamp("Error sending to Discord: Timeout.")
    except requests.exceptions.RequestException as e:
        log_timestamp(f"Error sending to Discord: {e}")

# --- Main Worker Function ---
def queue_worker():
    """
    A long-running worker that processes items from the webhook_queue.
    """
    log_timestamp("Queue worker thread started.")
    while True:
        try:
            # Wait for a payload to become available in the queue
            payload = webhook_queue.get()
            log_timestamp(f"Worker pulled payload from queue. Queue size is now: {webhook_queue.qsize()}")
            
            # Start processing the payload
            if payload.get("event") and payload["event"].get("activity"):
                for activity_item in payload["event"]["activity"]:
                    log_data = activity_item.get("log")
                    if log_data and log_data.get("decoded"):
                        decoded_event = log_data["decoded"]
                        event_name = decoded_event.get("name")
                        
                        if event_name == "DisputePrice":
                            # ... (All the parsing and embed creation logic from the previous script) ...
                            # This part is the same as before, just happening inside the worker
                            params = decoded_event.get("params", [])
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
                            if price_value_str == "0": disputed_answer = "p1 (e.g., NO)"
                            elif price_value_str == "1000000000000000000": disputed_answer = "p2 (e.g., YES)"
                            elif price_value_str == "500000000000000000": disputed_answer = "p3 (e.g., 0.5/INVALID)"

                            tx_hash = activity_item.get("hash", "N/A")
                            disputer_address = event_params.get("disputer", "N/A")
                            network = payload.get("event", {}).get("network", "ETH_MAINNET").upper()
                            etherscan_base = "https://polygonscan.com" if "POLYGON" in network or "MATIC" in network else "https://etherscan.io"
                            
                            display_title = human_readable_title if human_readable_title != "N/A" else f"Market Identifier: `{market_identifier_hex}`"
                            
                            embed = {
                                "title": "❌ Price Disputed ❌", "description": f"**Title/Market:** {display_title}\n", "color": 0xFF0000,
                                "fields": [
                                    {"name": "Disputed Outcome", "value": str(disputed_answer), "inline": True},
                                    {"name": "Disputer", "value": f"[{str(disputer_address)}]({etherscan_base}/address/{disputer_address})", "inline": True},
                                    {"name": "Transaction", "value": f"[{tx_hash[:12]}...]({etherscan_base}/tx/{tx_hash})", "inline": False},
                                ],
                                "footer": {"text": f"Network: {network}"}, "timestamp": payload.get("createdAt")
                            }
                            send_to_discord(embeds=[embed])

            # Mark the task as done
            webhook_queue.task_done()
        except Exception as e:
            log_timestamp(f"Error in queue_worker: {e}")
            # Ensure task_done is called even on error to prevent blocking
            webhook_queue.task_done()

# --- Webhook Endpoint (Now very fast) ---
@app.route('/alchemy-webhook', methods=['POST'])
def alchemy_webhook_receiver():
    log_timestamp("Webhook request received.")
    try:
        payload = request.json
        if not payload:
            log_timestamp("Received empty or invalid payload.")
            return jsonify({"status": "error", "message": "Empty or invalid payload"}), 400

        # Put the payload into the queue for the background worker to process
        webhook_queue.put(payload)
        log_timestamp(f"Payload added to queue. Queue size is now: {webhook_queue.qsize()}")

        # Immediately return a success response to Alchemy
        return jsonify({"status": "success", "message": "Webhook received and queued"}), 200
    except Exception as e:
        log_timestamp(f"Error handling initial request: {e}")
        return jsonify({"status": "error", "message": "Error handling initial request"}), 500

# --- Health Check ---
@app.route('/', methods=['GET'])
def health_check():
    return f"Webhook receiver is alive! Items in queue: {webhook_queue.qsize()}", 200