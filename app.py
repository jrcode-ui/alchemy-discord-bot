from flask import Flask, request, jsonify
import requests # For sending requests to Discord webhook
import json
import os
from datetime import datetime, timezone # For converting timestamp
import re # For parsing ancillaryData

# --- Configuration - PLEASE REPLACE WITH YOUR ACTUAL VALUES ---

# Discord Webhook URL (get this from your Discord server settings)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "YOUR_DISCORD_WEBHOOK_URL_HERE")

# Port for the Flask app to run on
FLASK_PORT = int(os.environ.get("FLASK_PORT", 5001))

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Helper Function to Decode Hex to String ---
def hex_to_string(hex_str):
    """Attempts to decode a hex string into a UTF-8 string."""
    if hex_str.startswith("0x"):
        hex_str = hex_str[2:]
    try:
        byte_array = bytes.fromhex(hex_str)
        return byte_array.decode('utf-8', errors='replace') # Use 'replace' for non-UTF-8 chars
    except ValueError:
        print(f"ValueError decoding hex: {hex_str}")
        return None # Or return the original hex string if preferred
    except Exception as e:
        print(f"Error decoding hex string {hex_str}: {e}")
        return None

# --- Helper Function to Extract Title from Ancillary Data ---
def extract_title_from_ancillary(ancillary_data_str):
    """
    Extracts the market title from the decoded ancillaryData string.
    Assumes title is prefixed with 'title: ' and ends before the next common field like ', description:' or newline.
    """
    if not ancillary_data_str:
        return None
    # Try to find "title: " (case-insensitive)
    match = re.search(r"title:\s*(.*?)(?:,\s*description:|, desc:|resolution_criteria:|\n|$)", ancillary_data_str, re.IGNORECASE | re.DOTALL)
    if match:
        title = match.group(1).strip()
        # Remove potential trailing garbage if it's very long or has unprintable chars
        # This is a heuristic, might need refinement
        if len(title) > 200: # Arbitrary length limit
            title = title[:200] + "..."
        return title.replace('\u0000', '').strip() # Remove null characters and strip
    return None

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

                    TARGET_DISPUTE_EVENT_NAME = "DisputePrice"

                    if event_name == TARGET_DISPUTE_EVENT_NAME:
                        print(f"Processing '{event_name}' event...")

                        event_params = {p["name"]: p["value"] for p in params}

                        market_identifier_hex = event_params.get("identifier", "N/A")
                        ancillary_data_hex = event_params.get("ancillaryData", "") # Get as hex

                        # Attempt to decode ancillaryData and extract title
                        human_readable_title = "N/A"
                        if ancillary_data_hex and ancillary_data_hex != "0x": # Check if not empty or just "0x"
                            ancillary_data_str = hex_to_string(ancillary_data_hex)
                            if ancillary_data_str:
                                print(f"Decoded Ancillary Data: {ancillary_data_str[:300]}...") # Log part of it
                                extracted_title = extract_title_from_ancillary(ancillary_data_str)
                                if extracted_title:
                                    human_readable_title = extracted_title
                                else:
                                    print("Could not extract title from ancillary data.")
                            else:
                                print("Failed to decode ancillary data hex.")
                        else:
                            print("Ancillary data is empty or '0x'.")


                        raw_proposed_price = event_params.get("proposedPrice")
                        disputed_answer = str(raw_proposed_price) # Default to raw string value

                        # Interpret proposedPrice: 0 as p1, 1*10^18 as p2, 0.5*10^18 as p3
                        # Assuming 18 decimals for price representation. Adjust if different.
                        price_value_str = str(raw_proposed_price)
                        if price_value_str == "0":
                            disputed_answer = "p1 (e.g., NO)"
                        elif price_value_str == "1000000000000000000": # 1 * 10^18
                            disputed_answer = "p2 (e.g., YES)"
                        elif price_value_str == "500000000000000000":  # 0.5 * 10^18
                            disputed_answer = "p3 (e.g., 0.5/INVALID)"
                        # else: it remains the raw string value

                        disputer_address = event_params.get("disputer", "N/A")
                        requester_address = event_params.get("requester", "N/A")
                        proposer_address = event_params.get("proposer", "N/A")
                        event_timestamp_unix = event_params.get("timestamp")

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

                        event_time_str = "N/A"
                        if event_timestamp_unix:
                            try:
                                event_time_str = datetime.fromtimestamp(int(event_timestamp_unix), timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
                            except (ValueError, TypeError):
                                pass

                        # --- Format Message for Discord ---
                        embed_title_text = f"❌ Price Disputed ❌"
                        # Use extracted title if available, otherwise fallback to identifier
                        display_title = human_readable_title if human_readable_title != "N/A" else f"Market Identifier: `{market_identifier_hex}`"
                        embed_description = f"**Title/Market:** {display_title}\n"

                        embed = {
                            "title": embed_title_text,
                            "description": embed_description,
                            "color": 0xFF0000,  # Red
                            "fields": [
                                {"name": "Disputed Outcome", "value": str(disputed_answer), "inline": True},
                                {"name": "Disputer", "value": f"[{str(disputer_address)}]({disputer_link})", "inline": True},
                                {"name": "Requester", "value": str(requester_address), "inline": True},
                                {"name": "Proposer", "value": str(proposer_address), "inline": True},
                                {"name": "Event Timestamp", "value": event_time_str, "inline": False},
                                # {"name": "Ancillary Data (Raw)", "value": f"`{ancillary_data_hex[:50]}{'...' if len(ancillary_data_hex)>50 else ''}`", "inline": False},
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