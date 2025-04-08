import requests
import time
import json
import os
import math
import argparse
import csv
from datetime import datetime
from dotenv import load_dotenv

# --- Configuration ---
load_dotenv()
API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
if not API_KEY:
    raise ValueError("Google Maps API Key not found. Set GOOGLE_MAPS_API_KEY environment variable.")

PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"

# Define the fields to request from Place Details API
# This list aims for comprehensive data across Basic, Contact, and Atmosphere categories.
# Cost is incurred PER REQUEST based on the *categories* of data requested.
# Basic: Often free (address_components, adr_address, business_status, formatted_address, geometry, icon, name, place_id, plus_code, type, url, utc_offset, vicinity)
# Contact: Has cost (current_opening_hours, formatted_phone_number, international_phone_number, opening_hours, website)
# Atmosphere: Has cost (price_level, rating, reviews, user_ratings_total)
# NOTE: Requesting 'reviews' can significantly increase response size. Included here for completeness, remove if not needed.
FIELDS_TO_REQUEST = [
    # Basic Data
    "place_id", "name", "formatted_address", "address_components",
    "geometry/location/lat", "geometry/location/lng", # Request geometry to get location
    "types", "business_status", "url", "vicinity",
    "plus_code/compound_code", "plus_code/global_code", # Request plus_code to get codes
    # Note: utc_offset_minutes appears to be unsupported by the current API despite documentation
    # Contact Data
    "international_phone_number", "website", "opening_hours", # Requesting opening_hours gives periods, weekday_text, possibly open_now
    "current_opening_hours", # More detailed current hours info
    # Atmosphere Data
    "rating", "user_ratings_total", "price_level", "reviews" 
]
# Generate the 'fields' parameter string
FIELDS_PARAM = ",".join(field.split('/')[0] for field in FIELDS_TO_REQUEST) # Only need top-level field name for API

# Define the headers for the output CSV file (flattened structure)
CSV_HEADERS = [
    "place_id", "name", "formatted_address", "lat", "lng", "business_status",
    "rating", "user_ratings_total", "price_level", "international_phone_number",
    "website", "url", "vicinity", "plus_code_compound", "plus_code_global",
    "types", "address_components_json",
    "opening_hours_json", "current_opening_hours_json", "reviews_json"
]

# Delay between API calls to be courteous and avoid hitting rapid rate limits
API_DELAY_SECONDS = 0.1 # Adjust as needed, increase if facing rate issues

# --- Argument Parser ---
parser = argparse.ArgumentParser(description="Fetch Place Details for a list of Place IDs and save to CSV.")
parser.add_argument("input_file", help="Path to the text file containing Place IDs (one per line).")
parser.add_argument("-o", "--output-file", default=None,
                    help="Path to the output CSV file (default: details_summary_TIMESTAMP.csv).")
args = parser.parse_args()

# --- Helper Functions ---

def get_place_details(api_key, place_id, fields_param):
    """Fetch details for a single Place ID."""
    params = {
        "place_id": place_id,
        "fields": fields_param,
        "key": api_key
    }
    try:
        response = requests.get(PLACE_DETAILS_URL, params=params)
        data = response.json()
        if data.get("status") == "OK":
            return data.get("result")
        else:
            print(f"Error fetching details for {place_id}: {data.get('status')} - {data.get('error_message', 'No error message')}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Network error fetching details for {place_id}: {e}")
        return None
    except json.JSONDecodeError:
        print(f"JSON decode error fetching details for {place_id}. Response text: {response.text[:100]}...")
        return None

def flatten_place_data(place_data, headers):
    """Flatten the nested JSON data from Place Details into a dictionary for CSV."""
    flat_data = {}

    # Direct mapping for simple fields
    flat_data["place_id"] = place_data.get("place_id", "")
    flat_data["name"] = place_data.get("name", "")
    flat_data["formatted_address"] = place_data.get("formatted_address", "")
    flat_data["business_status"] = place_data.get("business_status", "")
    flat_data["rating"] = place_data.get("rating", "")
    flat_data["user_ratings_total"] = place_data.get("user_ratings_total", "")
    flat_data["price_level"] = place_data.get("price_level", "")
    flat_data["international_phone_number"] = place_data.get("international_phone_number", "")
    flat_data["website"] = place_data.get("website", "")
    flat_data["url"] = place_data.get("url", "")
    flat_data["vicinity"] = place_data.get("vicinity", "")
    # Note: utc_offset_minutes appears to be unsupported by the current API despite documentation

    # Nested fields
    flat_data["lat"] = place_data.get("geometry", {}).get("location", {}).get("lat", "")
    flat_data["lng"] = place_data.get("geometry", {}).get("location", {}).get("lng", "")
    flat_data["plus_code_compound"] = place_data.get("plus_code", {}).get("compound_code", "")
    flat_data["plus_code_global"] = place_data.get("plus_code", {}).get("global_code", "")

    # Array/Object fields (serialize to JSON string or join)
    flat_data["types"] = "|".join(place_data.get("types", []))
    flat_data["address_components_json"] = json.dumps(place_data.get("address_components", []), ensure_ascii=False)
    flat_data["opening_hours_json"] = json.dumps(place_data.get("opening_hours", {}), ensure_ascii=False)
    flat_data["current_opening_hours_json"] = json.dumps(place_data.get("current_opening_hours", {}), ensure_ascii=False)
    flat_data["reviews_json"] = json.dumps(place_data.get("reviews", []), ensure_ascii=False)
    
    # Ensure all headers exist in the output dict, even if data was missing
    for header in headers:
        if header not in flat_data:
            flat_data[header] = ""
            
    return flat_data

# --- Main Execution ---
def main():
    print("--- Starting Place Details Extraction ---")

    input_file = args.input_file
    output_file = args.output_file

    if not output_file:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f"place_details_summary_{timestamp}.csv"

    # Read Place IDs from input file
    place_ids_to_fetch = []
    try:
        with open(input_file, 'r') as f:
            for line in f:
                place_id = line.strip()
                if place_id: # Ignore empty lines
                    place_ids_to_fetch.append(place_id)
        print(f"Read {len(place_ids_to_fetch)} Place IDs from {input_file}")
    except FileNotFoundError:
        print(f"Error: Input file not found at {input_file}")
        return
    except Exception as e:
        print(f"Error reading input file {input_file}: {e}")
        return

    if not place_ids_to_fetch:
        print("No Place IDs found in the input file. Exiting.")
        return

    # --- Process Place IDs ---
    total_ids = len(place_ids_to_fetch)
    processed_count = 0
    error_count = 0
    start_time = time.time()

    print(f"Requesting fields: {FIELDS_PARAM}")
    print(f"Outputting to: {output_file}")

    try:
        with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=CSV_HEADERS)
            writer.writeheader()

            for place_id in place_ids_to_fetch:
                print(f"Processing {processed_count + 1}/{total_ids}: {place_id} ... ", end='', flush=True)
                
                details = get_place_details(API_KEY, place_id, FIELDS_PARAM)

                if details:
                    flat_data = flatten_place_data(details, CSV_HEADERS)
                    writer.writerow(flat_data)
                    print("OK")
                else:
                    error_count += 1
                    print("Failed") # Error message printed in get_place_details

                processed_count += 1
                
                # Add delay
                time.sleep(API_DELAY_SECONDS)

    except IOError as e:
        print(f"\nError writing to output file {output_file}: {e}")
        return
    except Exception as e:
        print(f"\nAn unexpected error occurred during processing: {e}")
        import traceback
        traceback.print_exc()
        return
    finally:
        elapsed = time.time() - start_time
        print("\n--- Extraction Summary ---")
        print(f"Total Place IDs processed: {processed_count}/{total_ids}")
        print(f"Successfully fetched details for: {processed_count - error_count}")
        print(f"Errors encountered: {error_count}")
        print(f"Total runtime: {elapsed:.2f} seconds")
        if error_count < processed_count:
            print(f"Results saved to: {output_file}")
        else:
             print(f"No successful results were saved.")


if __name__ == "__main__":
    main()
