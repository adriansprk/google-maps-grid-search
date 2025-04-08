import requests
import time
import json
import os
import math
import random
import argparse
from datetime import datetime
from dotenv import load_dotenv
from collections import deque

# --- Command Line Arguments ---
parser = argparse.ArgumentParser(description="Extract place data from Google Maps API using grid-based search")
parser.add_argument("--dry-run", action="store_true", help="Run in dry run mode with mock responses")
parser.add_argument("--test-area", choices=["alexanderplatz", "tiergarten", "kreuzberg", "friedrichstrasse", "all"], 
                    help="Run on specific test area instead of full Berlin")
parser.add_argument("--max-calls", type=int, default=0, 
                    help="Maximum API calls to make before stopping (0 = unlimited)")
parser.add_argument("--visualize", action="store_true", help="Generate visualization maps of the search")
parser.add_argument("--param-test", action="store_true", help="Run parameter sensitivity testing")
parser.add_argument("--combine-maps", nargs='+', help="Combine multiple saved map data files into one visualization")
parser.add_argument("--place-type", type=str, default="physiotherapist", 
                    help="The type of place to search for (e.g., restaurant, cafe, gym)")
parser.add_argument("--location", type=str, default="Berlin, Germany",
                    help="The location to search in (e.g., 'New York, NY', 'London, UK')")
args = parser.parse_args()

# --- Configuration ---
load_dotenv()
API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
if not API_KEY:
    raise ValueError("Google Maps API Key not found.")

# API URLs
BASE_NEARBY_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"
PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"

# Use command line arguments if provided, otherwise use defaults
TARGET_LOCATION = args.location
PLACE_TYPE = args.place_type

# Critical API parameters
MAX_RADIUS = 5000  # Maximum allowed radius in meters (5km)
INITIAL_RADIUS = 750  # Initial search radius for standard grid points
INITIAL_GRID_STEP = 750  # Distance between standard grid points

# Correct understanding of Google Places API result limits
NEARBY_SEARCH_SINGLE_PAGE_SIZE = 20  # Each page contains up to 20 results
NEARBY_SEARCH_ACTUAL_LIMIT = 60  # The true limit across all pages is ~60 results
SUBDIVISION_THRESHOLD = 45  # Subdivide when we get close to the true limit (conservatively set)

# Refinement parameters
MINI_RADIUS_FACTOR = 3.0  # mini_radius = original_radius / MINI_RADIUS_FACTOR
MINI_GRID_OVERLAP_FACTOR = 1.0  # mini_step = mini_radius * MINI_GRID_OVERLAP_FACTOR

# Define test areas for small-scale testing
TEST_AREAS = {
    "alexanderplatz": {
        "name": "Alexanderplatz (Dense)",
        "bounds": (52.5150, 13.4050, 52.5250, 13.4150)
    },
    "tiergarten": {
        "name": "Tiergarten (Sparse)",
        "bounds": (52.5100, 13.3500, 52.5200, 13.3600)
    },
    "kreuzberg": {
        "name": "Kreuzberg (Mixed)",
        "bounds": (52.4900, 13.3900, 52.5000, 13.4000)
    },
    "friedrichstrasse": {
        "name": "Friedrichstraße Area",
        "bounds": (52.5000, 13.3850, 52.5300, 13.3950)
    }
}

# API backoff parameters
BASE_DELAY = 1.0  # Base delay in seconds
MAX_DELAY = 60.0  # Maximum delay in seconds
CURRENT_DELAY = BASE_DELAY
CONSECUTIVE_ERRORS = 0

# Global counters
GLOBAL_API_CALLS = 0

# Progress states
POINT_STATE_PENDING = "pending"
POINT_STATE_REFINING = "refining" 
POINT_STATE_COMPLETE = "complete"

# Define global visualization data structure
place_ids_with_coords = []  # Will store tuples of (place_id, lat, lng) for visualization

# Helper functions
def calculate_max_distance_meters(bounds):
    """Calculate the maximum distance from center to corner in meters"""
    min_lat, min_lng, max_lat, max_lng = bounds
    center_lat = (min_lat + max_lat) / 2
    center_lng = (min_lng + max_lng) / 2
    
    # Calculate distances to each corner
    corners = [
        (min_lat, min_lng),
        (min_lat, max_lng),
        (max_lat, min_lng),
        (max_lat, max_lng)
    ]
    
    max_distance = 0
    for corner_lat, corner_lng in corners:
        distance = haversine_distance(center_lat, center_lng, corner_lat, corner_lng)
        max_distance = max(max_distance, distance)
    
    return max_distance

def haversine_distance(lat1, lng1, lat2, lng2):
    """Calculate haversine distance between two points in meters"""
    # Earth's radius in meters
    R = 6371000
    
    # Convert latitude and longitude from degrees to radians
    lat1, lng1, lat2, lng2 = map(math.radians, [lat1, lng1, lat2, lng2])
    
    # Haversine formula
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    distance = R * c
    
    return distance

def meters_to_lat_degrees(meters):
    """Convert meters to latitude degrees (approximately)"""
    return meters / 111320  # 1 latitude degree is approximately 111.32 km

def meters_to_lng_degrees(meters, lat):
    """Convert meters to longitude degrees at the specified latitude"""
    # At the equator, 1 longitude degree is approximately 111.32 km
    # As we move away from the equator, this decreases by the cosine of the latitude
    return meters / (111320 * math.cos(math.radians(lat)))

def get_bounding_box(api_key, location):
    """Get the bounding box for a location using the Google Maps Geocoding API."""
    print(f"Getting bounding box for {location}...")
    
    params = {
        "address": location,
        "key": api_key
    }
    
    try:
        response = requests.get(GEOCODING_URL, params=params)
        data = response.json()
        
        if data["status"] != "OK":
            print(f"Error: {data['status']}")
            return None
        
        # Get viewport (which is a bounding box)
        viewport = data["results"][0]["geometry"]["viewport"]
        
        # Extract coordinates
        northeast = viewport["northeast"]
        southwest = viewport["southwest"]
        
        # Return as (min_lat, min_lng, max_lat, max_lng)
        return (southwest["lat"], southwest["lng"], northeast["lat"], northeast["lng"])
    except Exception as e:
        print(f"Error getting bounding box: {e}")
        return None

def generate_grid_points(bounds, step_meters):
    """Generate grid points covering a bounding box with even spacing."""
    min_lat, min_lng, max_lat, max_lng = bounds
    
    # Calculate step sizes in degrees based on the step in meters
    # For latitude, the conversion is roughly constant
    lat_step = meters_to_lat_degrees(step_meters)
    
    points = []
    current_lat = min_lat
    
    while current_lat <= max_lat:
        # For longitude, the conversion depends on the current latitude
        lng_step = meters_to_lng_degrees(step_meters, current_lat)
        current_lng = min_lng
        
        while current_lng <= max_lng:
            # Store points with consistent precision
            points.append((round(current_lat, 6), round(current_lng, 6)))
            current_lng += lng_step
            
        current_lat += lat_step
    
    print(f"Generated {len(points)} grid points with approximate step of {step_meters} meters.")
    return points

def generate_mini_grid(center_point, area_radius, step_meters):
    """Generate a grid of points within a circular area around a center point."""
    center_lat, center_lng = center_point
    
    # Calculate step sizes in degrees
    lat_step = meters_to_lat_degrees(step_meters)
    lng_step = meters_to_lng_degrees(step_meters, center_lat)
    
    # Calculate the number of steps needed in each direction
    # Convert radius to degree offsets (approximate)
    lat_radius = meters_to_lat_degrees(area_radius)
    lng_radius = meters_to_lng_degrees(area_radius, center_lat)
    
    # Number of steps in each direction (rounded up to ensure coverage)
    lat_steps = math.ceil(lat_radius / lat_step)
    lng_steps = math.ceil(lng_radius / lng_step)
    
    # Generate grid points in a square that encompasses the circle
    points = []
    for i in range(-lat_steps, lat_steps + 1):
        for j in range(-lng_steps, lng_steps + 1):
            point_lat = center_lat + i * lat_step
            point_lng = center_lng + j * lng_step
            
            # Optional: Add only if within the circular area
            # (This is more accurate but slightly more complex)
            if haversine_distance(center_lat, center_lng, point_lat, point_lng) <= area_radius:
                points.append((round(point_lat, 6), round(point_lng, 6)))
    
    return points

def generate_mock_response(lat, lng, radius, place_type, next_page_token=None):
    """Generate a realistic mock response based on location and simulated density."""
    
    # If next_page_token is provided, determine which pagination page we're on
    if next_page_token:
        if next_page_token.endswith("1"):
            pagination_page = 1
        elif next_page_token.endswith("2"):
            pagination_page = 2
        else:
            pagination_page = int(next_page_token.split("_")[-1])
    else:
        pagination_page = 0
    
    # Define test areas with more precise density information
    dense_areas = [
        {"center": (52.520008, 13.404954), "radius": 2000, "density": "high"},    # Alexanderplatz
        {"center": (52.504556, 13.391794), "radius": 1500, "density": "medium"},  # Kreuzberg
        {"center": (52.531, 13.386), "radius": 1200, "density": "high"},          # Mitte
        {"center": (52.5182, 13.3765), "radius": 1800, "density": "medium"}       # Tiergarten
    ]
    
    # Determine density based on proximity to defined areas
    area_density = "low"  # Default
    min_distance = float('inf')
    closest_area = None
    
    for area in dense_areas:
        distance = haversine_distance(lat, lng, area["center"][0], area["center"][1])
        if distance <= area["radius"]:
            # Within the defined area
            area_density = area["density"]
            closest_area = area
            break
        elif distance < min_distance:
            min_distance = distance
            closest_area = area
    
    # If not in any defined area, use distance-based falloff from closest area
    if closest_area and not area_density:
        # The further from a dense area, the lower the density
        normalized_distance = min(1.0, min_distance / (closest_area["radius"] * 2))
        if normalized_distance < 0.3:
            area_density = "medium"
        elif normalized_distance < 0.6:
            area_density = "low"
        else:
            area_density = "sparse"
    
    # Set probabilities based on density
    if area_density == "high":
        should_trigger_refinement = random.random() < 0.8
        result_count_multiplier = 0.8  # 80% of max results
    elif area_density == "medium":
        should_trigger_refinement = random.random() < 0.4
        result_count_multiplier = 0.5  # 50% of max results
    elif area_density == "low":
        should_trigger_refinement = random.random() < 0.2
        result_count_multiplier = 0.3  # 30% of max results
    else:  # sparse
        should_trigger_refinement = random.random() < 0.05
        result_count_multiplier = 0.1  # 10% of max results
    
    # Occasionally simulate API errors (more likely in high-density areas)
    error_probability = 0.05 if area_density == "high" else 0.03
    if random.random() < error_probability:
        return {"status": "OVER_QUERY_LIMIT"}
    
    # Determine result counts based on pagination and density
    if pagination_page == 0:
        if should_trigger_refinement:
            # Will trigger pagination (full page of 20)
            num_results = 20
            next_token = f"mock_token_page_1_{int(time.time())}"
        else:
            # Random number of results scaled by density
            max_first_page = int(20 * result_count_multiplier)
            num_results = random.randint(0, max_first_page)
            next_token = None if num_results < 15 else f"mock_token_page_1_{int(time.time())}"
    # Second page
    elif pagination_page == 1:
        if should_trigger_refinement:
            # Will trigger pagination again (full page of 20)
            num_results = 20
            next_token = f"mock_token_page_2_{int(time.time())}"
        else:
            # Random remaining results
            max_second_page = int(15 * result_count_multiplier)
            num_results = random.randint(5, max_second_page)
            next_token = None
    # Third page (if we should trigger refinement)
    elif pagination_page == 2 and should_trigger_refinement:
        # Enough to trigger the threshold
        min_results = int(SUBDIVISION_THRESHOLD / 3)  # At least enough to reach threshold when combined
        max_results = 20
        num_results = random.randint(min_results, max_results)
        next_token = None
    else:
        num_results = 0
        next_token = None
    
    # Generate fake place IDs
    results = []
    base_hash = hash(f"{lat}_{lng}_{pagination_page}")
    for i in range(num_results):
        # Create a deterministic but varied place ID
        place_id = f"mock_place_{area_density}_{base_hash % 10000}_{i}"
        name = f"Physio {area_density.title()} {base_hash % 1000}-{i}"
        
        # Generate location near the search point, with more variance in low-density areas
        location_variance = 0.005 if area_density == "sparse" else 0.002
        place_lat = lat + (random.random() - 0.5) * location_variance
        place_lng = lng + (random.random() - 0.5) * location_variance
        
        # Create a more comprehensive mock result with all the fields we want to track
        place_result = {
            "place_id": place_id, 
            "name": name,
            "geometry": {
                "location": {
                    "lat": place_lat,
                    "lng": place_lng
                }
            },
            "vicinity": f"{area_density.title()} Street, Berlin",
            "types": ["physiotherapist", "health", "point_of_interest", "establishment"],
            "business_status": random.choice(["OPERATIONAL", "CLOSED_TEMPORARILY", "CLOSED_PERMANENTLY"]),
            "rating": round(3 + random.random() * 2, 1),  # Random rating between 3.0 and 5.0
            "user_ratings_total": random.randint(1, 150),
            "plus_code": {
                "compound_code": f"XXX+XX Berlin, Germany",
                "global_code": f"9F4MXXX+XX"
            }
        }
        
        results.append(place_result)
    
    # Return appropriately structured mock response
    if num_results == 0:
        return {"status": "ZERO_RESULTS"}
    else:
        return {
            "status": "OK",
            "results": results,
            "next_page_token": next_token
        }

def perform_nearby_search(api_key, lat, lng, radius, place_type, next_page_token=None):
    """Perform a nearby search using the Google Maps Places API."""
    global GLOBAL_API_CALLS, CONSECUTIVE_ERRORS, CURRENT_DELAY
    
    # Check if we're in dry run mode
    if args.dry_run:
        # Don't count as an actual API call
        print(f"[DRY RUN] Would search: lat={lat}, lng={lng}, radius={radius}, token={next_page_token}")
        return generate_mock_response(lat, lng, radius, place_type, next_page_token)
    
    # Check API call limit if set
    if args.max_calls > 0 and GLOBAL_API_CALLS >= args.max_calls:
        print(f"\n*** Reached maximum API call limit of {args.max_calls}. Stopping. ***")
        raise Exception("API call limit reached")
        
    # Prepare request parameters
    if next_page_token:
        # Use page token for pagination
        params = {
            "pagetoken": next_page_token,
            "key": api_key
        }
    else:
        # Initial search
        params = {
            "location": f"{lat},{lng}",
            "radius": radius,
            "type": place_type,
            "key": api_key
        }
    
    # Track real API calls
    GLOBAL_API_CALLS += 1
    
    try:
        response = requests.get(BASE_NEARBY_SEARCH_URL, params=params)
        data = response.json()
        
        # Handle rate limiting with exponential backoff
        if data.get("status") == "OVER_QUERY_LIMIT":
            CONSECUTIVE_ERRORS += 1
            CURRENT_DELAY = min(MAX_DELAY, BASE_DELAY * (2 ** CONSECUTIVE_ERRORS))
            print(f"Rate limit exceeded. Backing off for {CURRENT_DELAY} seconds.")
            time.sleep(CURRENT_DELAY)
            # Retry with same parameters
            return perform_nearby_search(api_key, lat, lng, radius, place_type, next_page_token)
        else:
            # On success, gradually reduce delay
            CONSECUTIVE_ERRORS = max(0, CONSECUTIVE_ERRORS - 1)
            CURRENT_DELAY = max(BASE_DELAY, CURRENT_DELAY / 2)
            
        return data
    except Exception as e:
        print(f"Error in nearby search: {e}")
        return {"status": "REQUEST_FAILED", "error_message": str(e)}

def process_search_results(data, all_place_ids, place_ids_this_point):
    """Process search results and update place ID sets."""
    results = data.get('results', [])
    newly_added_count = 0
    
    for place in results:
        place_id = place.get('place_id')
        if place_id:
            place_ids_this_point.add(place_id)
            # Check if this is a new place ID but don't add it to all_place_ids yet
            # This will be done when saving to file
            if place_id not in all_place_ids:
                newly_added_count += 1
                
            # Extract and save the full place data
            save_detailed_place_data(place)
    
    return newly_added_count, len(results)

def save_detailed_place_data(place):
    """Save comprehensive place data to a JSON file."""
    # Ensure directory exists
    detailed_data_dir = "detailed_place_data"
    os.makedirs(detailed_data_dir, exist_ok=True)
    
    place_id = place.get('place_id')
    if not place_id:
        return
        
    # Extract essential fields
    place_data = {
        "place_id": place_id,
        "name": place.get('name', ''),
        "location": {
            "lat": place.get('geometry', {}).get('location', {}).get('lat', None),
            "lng": place.get('geometry', {}).get('location', {}).get('lng', None)
        },
        "types": place.get('types', []),
        "business_status": place.get('business_status', ''),
        "rating": place.get('rating', None),
        "user_ratings_total": place.get('user_ratings_total', None),
        "plus_code": place.get('plus_code', {}),
        "vicinity": place.get('vicinity', '')
    }
    
    # Save to JSON file with place_id as filename
    output_file = os.path.join(detailed_data_dir, f"{place_id}.json")
    with open(output_file, 'w') as f:
        json.dump(place_data, f, indent=2)

def create_summary_csv(output_dir="detailed_place_data", target_location="", mode=""):
    """Create a comprehensive CSV summary of all collected place data."""
    import csv
    import glob
    
    # Generate an appropriate filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    location_slug = target_location.split(',')[0].lower().replace(' ', '_') if target_location else "all"
    csv_filename = f"physiotherapist_summary_{location_slug}_{mode}_{timestamp}.csv"
    
    # Define CSV headers
    headers = [
        "place_id", "name", "lat", "lng", "business_status", 
        "rating", "user_ratings_total", "vicinity", "types"
    ]
    
    try:
        # Get all JSON files in the directory
        json_files = glob.glob(os.path.join(output_dir, "*.json"))
        
        if not json_files:
            print("No detailed place data found to summarize.")
            return
        
        # Write to CSV
        with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=headers)
            writer.writeheader()
            
            for json_file in json_files:
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        place_data = json.load(f)
                        
                    # Transform data for CSV
                    csv_row = {
                        "place_id": place_data.get("place_id", ""),
                        "name": place_data.get("name", ""),
                        "lat": place_data.get("location", {}).get("lat", ""),
                        "lng": place_data.get("location", {}).get("lng", ""),
                        "business_status": place_data.get("business_status", ""),
                        "rating": place_data.get("rating", ""),
                        "user_ratings_total": place_data.get("user_ratings_total", ""),
                        "vicinity": place_data.get("vicinity", ""),
                        "types": "|".join(place_data.get("types", []))
                    }
                    
                    writer.writerow(csv_row)
                except Exception as e:
                    print(f"Error processing {json_file}: {e}")
        
        print(f"CSV summary created: {csv_filename}")
        return csv_filename
    except Exception as e:
        print(f"Error creating CSV summary: {e}")
        return None

def perform_search_at_point(point_coords, radius, all_place_ids, output_file):
    """Perform search at a given point with pagination, save results, and return stats."""
    global place_ids_with_coords
    
    lat, lng = point_coords
    place_ids_this_point = set()
    api_calls = 0
    total_results = 0
    newly_added_total = 0
    
    # First search request
    data = perform_nearby_search(API_KEY, lat, lng, radius, PLACE_TYPE)
    api_calls += 1
    
    status = data.get('status')
    
    if status == 'OK':
        newly_added, results_count = process_search_results(data, all_place_ids, place_ids_this_point)
        total_results += results_count
        newly_added_total += newly_added
        
        print(f"Found {results_count} results on first page ({newly_added} new unique IDs).")
        
        # Process location data for visualization
        if args.visualize:
            extract_place_coordinates(data)
        
        # Handle pagination
        next_page_token = data.get('next_page_token')
        pagination_count = 1
        
        # Process additional pages if available
        while next_page_token:
            # Wait before requesting the next page (required by Google)
            time.sleep(2)
            
            data = perform_nearby_search(API_KEY, lat, lng, radius, PLACE_TYPE, next_page_token)
            api_calls += 1
            pagination_count += 1
            
            if data.get('status') == 'OK':
                newly_added, results_count = process_search_results(data, all_place_ids, place_ids_this_point)
                total_results += results_count
                newly_added_total += newly_added
                
                print(f"Page {pagination_count}: Found {results_count} results ({newly_added} new unique IDs).")
                
                # Process location data for this page too
                if args.visualize:
                    extract_place_coordinates(data)
                    
                next_page_token = data.get('next_page_token')
            else:
                print(f"Error on page {pagination_count}: {data.get('status')}")
                break
    
    elif status == 'ZERO_RESULTS':
        print("No results found at this point.")
    
    elif status == 'OVER_QUERY_LIMIT':
        print("WARNING: Exceeded query limit. Waiting to retry...")
        time.sleep(60)  # Wait for quota to reset
        return perform_search_at_point(point_coords, radius, all_place_ids, output_file)  # Retry
    
    else:
        print(f"Error: {status}")
        if 'error_message' in data:
            print(f"Error message: {data['error_message']}")
        return 0, 0, False, set()  # Failed
    
    # Save the IDs found at this point
    save_place_ids(place_ids_this_point, all_place_ids, output_file)
    
    return api_calls, total_results, total_results >= SUBDIVISION_THRESHOLD, place_ids_this_point

def extract_place_coordinates(data):
    """Extract coordinates from place results for visualization."""
    global place_ids_with_coords
    
    try:
        for place in data.get('results', []):
            if 'place_id' in place and 'geometry' in place and 'location' in place['geometry']:
                place_id = place['place_id']
                place_lat = place['geometry']['location']['lat']
                place_lng = place['geometry']['location']['lng']
                
                # Only add if this place_id isn't already in our visualization data
                if not any(pid == place_id for pid, _, _ in place_ids_with_coords):
                    place_ids_with_coords.append((place_id, place_lat, place_lng))
            else:
                print("Warning: Place data missing geometry information")
    except Exception as e:
        print(f"Error extracting place coordinates: {e}")

def perform_refined_search_at_point(point_coords, radius, all_place_ids, output_file):
    """Perform a refined search at a mini-grid point."""
    api_calls, total_results, exceeded_threshold, place_ids = perform_search_at_point(
        point_coords, radius, all_place_ids, output_file
    )
    
    # For mini-grid points, we don't need to check if threshold is exceeded
    # as we're already in the refinement process
    
    return api_calls

def save_place_ids(new_place_ids, all_place_ids, output_file):
    """Save new place IDs to the output file."""
    saved_count = 0
    with open(output_file, 'a') as f:
        for place_id in new_place_ids:
            if place_id not in all_place_ids:  # Only save IDs not already saved
                f.write(f"{place_id}\n")
                all_place_ids.add(place_id)  # Update the set
                saved_count += 1
    
    if saved_count > 0:
        print(f"Saved {saved_count} new place IDs to {output_file}")

def save_progress_point(point_coords, grid_type, state, progress_file, timestamp=None):
    """Save a processed point to the progress file with state information."""
    lat, lng = point_coords
    if timestamp is None:
        timestamp = int(time.time())
    with open(progress_file, 'a') as f:
        f.write(f"{lat},{lng},{grid_type},{state},{timestamp}\n")

def load_progress(progress_file, output_file):
    """Load progress from previous runs with enhanced state tracking."""
    completed_points = set()  # Points fully processed
    refining_points = set()   # Points in refining state
    all_place_ids = set()
    searched_mini_areas = set()  # Mini areas already searched (for overlap mitigation)
    
    # Load processed points
    if os.path.exists(progress_file):
        with open(progress_file, 'r') as f:
            for line in f:
                parts = line.strip().split(',')
                if len(parts) >= 4:  # lat, lng, grid_type, state, [timestamp]
                    lat, lng, grid_type, state = float(parts[0]), float(parts[1]), parts[2], parts[3]
                    point_key = (round(lat, 6), round(lng, 6), grid_type)
                    
                    if state == POINT_STATE_COMPLETE:
                        completed_points.add(point_key)
                    elif state == POINT_STATE_REFINING:
                        refining_points.add(point_key)
                        
                    # Track mini-grid points for overlap mitigation
                    if grid_type == "mini":
                        searched_mini_areas.add((round(lat, 6), round(lng, 6)))
    
    # Load all place IDs found
    if os.path.exists(output_file):
        with open(output_file, 'r') as f:
            for line in f:
                place_id = line.strip()
                if place_id:
                    all_place_ids.add(place_id)
    
    print(f"Loaded {len(completed_points)} completed points, {len(refining_points)} refining points, and {len(all_place_ids)} unique place IDs.")
    return completed_points, refining_points, searched_mini_areas, all_place_ids

def visualize_search_results(grid_points, refinement_points, place_ids_with_coords, output_file, additional_map_data=None):
    """Visualize the search grid, refinements, and results using folium if available.
    
    Args:
        grid_points: List of (lat, lng) tuples for standard grid points
        refinement_points: List of (lat, lng) tuples for mini-grid refinement points
        place_ids_with_coords: List of (place_id, lat, lng) tuples for found places
        output_file: Path to save the output HTML map
        additional_map_data: Optional list of (grid_points, refinement_points, place_ids_with_coords) 
                            from other runs to combine into one visualization
    """
    try:
        import folium
        from folium.plugins import HeatMap
    except ImportError:
        print("Folium not installed. Skipping visualization.")
        print("Install with: pip install folium")
        return
    
    print(f"Generating visualization map...")
    
    # Prepare colors for multiple datasets if combining maps
    color_sets = [
        {'grid': 'blue', 'refinement': 'red', 'places': 'green'},
        {'grid': 'darkblue', 'refinement': 'darkred', 'places': 'darkgreen'},
        {'grid': 'purple', 'refinement': 'orange', 'places': 'cadetblue'},
        {'grid': 'gray', 'refinement': 'black', 'places': 'darkpurple'},
        {'grid': 'lightblue', 'refinement': 'pink', 'places': 'lightgreen'}
    ]
    
    # Determine map center based on all data
    all_points = []
    if grid_points:
        all_points.extend(grid_points)
    if place_ids_with_coords:
        all_points.extend([(lat, lng) for _, lat, lng in place_ids_with_coords])
    
    # Add points from additional datasets if provided
    if additional_map_data:
        for dataset_idx, (add_grid, add_refine, add_places) in enumerate(additional_map_data):
            if add_grid:
                all_points.extend(add_grid)
            if add_places:
                all_points.extend([(lat, lng) for _, lat, lng in add_places])
    
    # Choose center point
    if all_points:
        # Calculate center of all points
        avg_lat = sum(lat for lat, _ in all_points) / len(all_points)
        avg_lng = sum(lng for _, lng in all_points) / len(all_points)
        center_lat, center_lng = avg_lat, avg_lng
    else:
        # Default to Berlin center
        center_lat, center_lng = 52.52, 13.41
    
    # Create the map with appropriate zoom level
    m = folium.Map(location=[center_lat, center_lng], zoom_start=12)
    
    # Process the primary dataset
    colors = color_sets[0]
    
    # Create layer groups for primary dataset
    grid_layer = folium.FeatureGroup(name="Standard Grid Points")
    refinement_layer = folium.FeatureGroup(name="Refinement Points")
    places_layer = folium.FeatureGroup(name="Physiotherapists")
    heatmap_layer = folium.FeatureGroup(name="Density Heatmap")
    
    # Plot standard grid points
    for lat, lng in grid_points:
        folium.CircleMarker(
            location=[lat, lng],
            radius=5,
            color=colors['grid'],
            fill=True,
            fill_opacity=0.4,
            popup=f"Standard Grid: {lat:.6f}, {lng:.6f}"
        ).add_to(grid_layer)
    
    # Plot refinement points
    for lat, lng in refinement_points:
        folium.CircleMarker(
            location=[lat, lng],
            radius=3,
            color=colors['refinement'],
            fill=True,
            fill_opacity=0.6,
            popup=f"Mini Grid: {lat:.6f}, {lng:.6f}"
        ).add_to(refinement_layer)
    
    # Plot found place IDs and prepare heatmap data
    heatmap_data = []
    for place_id, lat, lng in place_ids_with_coords:
        folium.Marker(
            location=[lat, lng],
            popup=f"Place ID: {place_id}",
            icon=folium.Icon(color=colors['places'], icon='info-sign')
        ).add_to(places_layer)
        heatmap_data.append([lat, lng, 1])
    
    # Process additional datasets if provided
    if additional_map_data:
        for dataset_idx, (add_grid, add_refine, add_places) in enumerate(additional_map_data):
            # Cycle through color sets for each additional dataset
            colors = color_sets[(dataset_idx + 1) % len(color_sets)]
            
            # Create layer groups for this dataset with distinct names
            add_grid_layer = folium.FeatureGroup(name=f"Grid Points (Dataset {dataset_idx+1})")
            add_refine_layer = folium.FeatureGroup(name=f"Refinement Points (Dataset {dataset_idx+1})")
            add_places_layer = folium.FeatureGroup(name=f"Physiotherapists (Dataset {dataset_idx+1})")
            
            # Add grid points
            for lat, lng in add_grid:
                folium.CircleMarker(
                    location=[lat, lng],
                    radius=5,
                    color=colors['grid'],
                    fill=True,
                    fill_opacity=0.4,
                    popup=f"Grid (Dataset {dataset_idx+1}): {lat:.6f}, {lng:.6f}"
                ).add_to(add_grid_layer)
            
            # Add refinement points
            for lat, lng in add_refine:
                folium.CircleMarker(
                    location=[lat, lng],
                    radius=3,
                    color=colors['refinement'],
                    fill=True,
                    fill_opacity=0.6,
                    popup=f"Refinement (Dataset {dataset_idx+1}): {lat:.6f}, {lng:.6f}"
                ).add_to(add_refine_layer)
            
            # Add place markers and extend heatmap data
            for place_id, lat, lng in add_places:
                folium.Marker(
                    location=[lat, lng],
                    popup=f"Place ID (Dataset {dataset_idx+1}): {place_id}",
                    icon=folium.Icon(color=colors['places'], icon='info-sign')
                ).add_to(add_places_layer)
                heatmap_data.append([lat, lng, 1])
            
            # Add these layers to the map
            add_grid_layer.add_to(m)
            add_refine_layer.add_to(m)
            add_places_layer.add_to(m)
    
    # Add heatmap if we have data
    if heatmap_data:
        HeatMap(heatmap_data).add_to(heatmap_layer)
    
    # Add all primary layers to map
    grid_layer.add_to(m)
    refinement_layer.add_to(m)
    places_layer.add_to(m)
    heatmap_layer.add_to(m)
    
    # Add layer control
    folium.LayerControl().add_to(m)
    
    # Save the map
    m.save(output_file)
    print(f"Map saved to {output_file}")

def save_map_data(grid_points, refinement_points, place_ids_with_coords, output_file):
    """Save visualization data to a file for later combination."""
    data = {
        "grid_points": grid_points,
        "refinement_points": refinement_points,
        "place_ids_with_coords": place_ids_with_coords
    }
    
    with open(output_file, 'w') as f:
        json.dump(data, f)
    
    print(f"Map data saved to {output_file}")

def load_map_data(input_file):
    """Load visualization data from a file."""
    try:
        with open(input_file, 'r') as f:
            data = json.load(f)
        
        return (
            data.get("grid_points", []), 
            data.get("refinement_points", []), 
            data.get("place_ids_with_coords", [])
        )
    except Exception as e:
        print(f"Error loading map data from {input_file}: {e}")
        return [], [], []

def test_parameter_sensitivity(test_area):
    """Run tests with different parameter combinations on a specific test area."""
    global INITIAL_RADIUS, INITIAL_GRID_STEP, SUBDIVISION_THRESHOLD, MINI_RADIUS_FACTOR
    
    print(f"\n======= PARAMETER SENSITIVITY TESTING =======")
    print(f"Test Area: {TEST_AREAS[test_area]['name']}")
    print(f"\nNOTE: Testing with reduced sample size for speed - using limited subset of grid points and mini-grid points.")
    print(f"     Results are indicative only and may vary from full-scale runs.\n")
    
    # Original values to restore after testing
    orig_radius = INITIAL_RADIUS
    orig_threshold = SUBDIVISION_THRESHOLD
    orig_factor = MINI_RADIUS_FACTOR
    
    # Parameters to test
    radius_values = [300, 500, 750]
    threshold_values = [45, 50, 55]
    mini_radius_factors = [2.5, 3.0, 4.0]
    
    # Results table
    results = []
    
    try:
        # Test each combination
        for radius in radius_values:
            for threshold in threshold_values:
                for factor in mini_radius_factors:
                    print(f"\nTesting: R={radius}m, T={threshold}, F={factor}")
                    
                    # Set global parameters
                    INITIAL_RADIUS = radius
                    SUBDIVISION_THRESHOLD = threshold
                    MINI_RADIUS_FACTOR = factor
                    
                    # Reset counters for this test
                    global GLOBAL_API_CALLS, place_ids_with_coords
                    GLOBAL_API_CALLS = 0
                    place_ids_with_coords = []  # Reset visualization data for each test
                    start_time = time.time()
                    
                    # Run the search on this test area with limited grid size
                    bounds = TEST_AREAS[test_area]["bounds"]
                    test_grid_points = generate_grid_points(bounds, INITIAL_RADIUS)
                    
                    # Take just 9 points for quick testing
                    if len(test_grid_points) > 9:
                        test_grid_points = test_grid_points[:9]
                        print(f"   Limited to {len(test_grid_points)} grid points for testing")
                    
                    # Track stats for this parameter set
                    unique_places = set()
                    refinements = 0
                    
                    # Create temporary files for this test
                    timestamp = int(time.time())
                    test_progress_file = f"temp_progress_{timestamp}.txt"
                    test_output_file = f"temp_output_{timestamp}.txt"
                    test_refinement_log = f"temp_refinement_{timestamp}.txt"
                    
                    try:
                        # Process the test grid with these parameters
                        with open(test_refinement_log, 'w') as refinement_log:
                            # Process each grid point
                            for i, point_coords in enumerate(test_grid_points):
                                # Perform the search at this point
                                try:
                                    api_calls, results_count, threshold_exceeded, place_ids = perform_search_at_point(
                                        point_coords, INITIAL_RADIUS, unique_places, test_output_file
                                    )
                                    
                                    # If threshold exceeded, do refinement
                                    if threshold_exceeded:
                                        refinements += 1
                                        refinement_log.write(f"{point_coords[0]},{point_coords[1]},{results_count}\n")
                                        
                                        # Calculate parameters for the refined search
                                        mini_radius = INITIAL_RADIUS / MINI_RADIUS_FACTOR
                                        mini_step = mini_radius * MINI_GRID_OVERLAP_FACTOR
                                        
                                        # Generate mini-grid points
                                        mini_grid_points = generate_mini_grid(point_coords, INITIAL_RADIUS, mini_step)
                                        
                                        # Process just a subset of mini-grid points for speed
                                        orig_count = len(mini_grid_points)
                                        if len(mini_grid_points) > 5:
                                            mini_grid_points = mini_grid_points[:5]
                                            print(f"      Limited from {orig_count} to {len(mini_grid_points)} mini-grid points")
                                        
                                        # Process each mini point
                                        for j, mini_point in enumerate(mini_grid_points):
                                            # Skip some to speed up testing
                                            if j % 2 == 0:
                                                continue
                                                
                                            calls_made = perform_refined_search_at_point(
                                                mini_point, mini_radius, unique_places, test_output_file
                                            )
                                except Exception as e:
                                    print(f"Error in parameter test: {e}")
                                    break
                                    
                        # Record results
                        elapsed = time.time() - start_time
                        results.append({
                            "radius": radius,
                            "threshold": threshold, 
                            "factor": factor,
                            "api_calls": GLOBAL_API_CALLS,
                            "unique_places": len(unique_places),
                            "refinements": refinements,
                            "time": elapsed
                        })
                        
                    finally:
                        # Clean up temporary files
                        for f in [test_progress_file, test_output_file, test_refinement_log]:
                            if os.path.exists(f):
                                try:
                                    os.remove(f)
                                except:
                                    pass
        
        # Print results table
        print("\nParameter Sensitivity Results (LIMITED SAMPLE SIZE):")
        print("----------------------------------------------------------------------")
        print("Radius | Threshold | Factor | Calls | Places | Refinements | Time (s)")
        print("----------------------------------------------------------------------")
        for r in sorted(results, key=lambda x: x['api_calls']):
            print(f"{r['radius']:6d} | {r['threshold']:9d} | {r['factor']:6.1f} | {r['api_calls']:5d} | {r['unique_places']:6d} | {r['refinements']:11d} | {r['time']:.1f}")
        
        # Also sort by unique places found
        print("\nSorted by Places Found (LIMITED SAMPLE SIZE):")
        print("----------------------------------------------------------------------")
        print("Radius | Threshold | Factor | Calls | Places | Refinements | Time (s)")
        print("----------------------------------------------------------------------")
        for r in sorted(results, key=lambda x: x['unique_places'], reverse=True):
            print(f"{r['radius']:6d} | {r['threshold']:9d} | {r['factor']:6.1f} | {r['api_calls']:5d} | {r['unique_places']:6d} | {r['refinements']:11d} | {r['time']:.1f}")
            
        # Calculate efficiency score (places per call)
        best_efficiency = 0
        best_config = None
        for r in results:
            if r['api_calls'] > 0:
                efficiency = r['unique_places'] / r['api_calls']
                if efficiency > best_efficiency:
                    best_efficiency = efficiency
                    best_config = r
                    
        if best_config:
            print(f"\nMost efficient configuration: R={best_config['radius']}m, T={best_config['threshold']}, F={best_config['factor']}")
            print(f"Found {best_config['unique_places']} places with {best_config['api_calls']} calls")
            print(f"Efficiency: {best_efficiency:.2f} places per API call")
            print("\nNote: These results are based on a limited sample and should be validated with a full run.")
            
    finally:
        # Restore original parameters
        INITIAL_RADIUS = orig_radius
        SUBDIVISION_THRESHOLD = orig_threshold
        MINI_RADIUS_FACTOR = orig_factor

# --- Main Function ---
def main():
    # Initialize global visualization data
    global place_ids_with_coords
    place_ids_with_coords = []
    
    # Special case: Combine existing maps
    if args.combine_maps:
        if len(args.combine_maps) < 2:
            print("Error: At least two map data files must be specified to combine maps.")
            return
            
        print(f"Combining {len(args.combine_maps)} maps...")
        output_file = f"combined_map_physiotherapist_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        map_data_file = f"combined_map_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        # Load the first dataset as the primary
        primary_grid, primary_refine, primary_places = load_map_data(args.combine_maps[0])
        
        # Load additional datasets
        additional_data = []
        for map_file in args.combine_maps[1:]:
            grid, refine, places = load_map_data(map_file)
            additional_data.append((grid, refine, places))
        
        # Generate combined visualization
        visualize_search_results(
            primary_grid, 
            primary_refine, 
            primary_places, 
            output_file,
            additional_data
        )
        
        # Save the combined data for future use
        all_grid = primary_grid.copy()
        all_refine = primary_refine.copy()
        all_places = primary_places.copy()
        
        for grid, refine, places in additional_data:
            all_grid.extend(grid)
            all_refine.extend(refine)
            all_places.extend(places)
        
        save_map_data(all_grid, all_refine, all_places, map_data_file)
        
        print(f"Combined visualization saved to {output_file}")
        print(f"Combined data saved to {map_data_file}")
        return
    
    print("--- Starting Place ID Extraction with Adaptive Refinement ---")
    print(f"Mode: {'DRY RUN (mock responses)' if args.dry_run else 'LIVE'}")
    
    # Special case: Parameter sensitivity testing
    if args.param_test:
        test_area = args.test_area or "alexanderplatz"
        test_parameter_sensitivity(test_area)
        return
    
    # Generate filenames based on parameters
    location_slug = TARGET_LOCATION.split(',')[0].lower().replace(' ', '_')
    if args.test_area:
        location_slug += f"_{args.test_area}"
    type_slug = PLACE_TYPE.replace(' ', '_')
    mode_slug = "dry_run" if args.dry_run else "live"
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Files for progress and results
    PROGRESS_FILE = f"progress_{type_slug}_{location_slug}_{mode_slug}.txt"
    OUTPUT_FILE = f"place_ids_{type_slug}_{location_slug}_{mode_slug}.txt"
    REFINEMENT_LOG = f"refinements_{type_slug}_{location_slug}_{mode_slug}.txt"
    NEAR_LIMIT_LOG = f"near_limit_log_{type_slug}_{location_slug}_{mode_slug}.txt"
    
    # Visualization files
    VISUALIZATION_FILE = f"map_{type_slug}_{location_slug}_{timestamp}.html"
    MAP_DATA_FILE = f"map_data_{type_slug}_{location_slug}_{timestamp}.json"  # Add this for saving map data
    
    # Ensure output directories exist
    os.makedirs(os.path.dirname(PROGRESS_FILE) if os.path.dirname(PROGRESS_FILE) else '.', exist_ok=True)
    os.makedirs(os.path.dirname(OUTPUT_FILE) if os.path.dirname(OUTPUT_FILE) else '.', exist_ok=True)
    os.makedirs("detailed_place_data", exist_ok=True)  # Ensure detailed data directory exists
    
    try:
        # Load progress from previous runs
        completed_points, refining_points, searched_mini_areas, all_place_ids = load_progress(PROGRESS_FILE, OUTPUT_FILE)
        
        # Get appropriate bounds based on mode
        if args.test_area and args.test_area != "all" and args.test_area in TEST_AREAS:
            # Use predefined test area
            bounds = TEST_AREAS[args.test_area]["bounds"]
            print(f"Using test area: {TEST_AREAS[args.test_area]['name']}")
        else:
            # Get bounds for target location
            bounds = get_bounding_box(API_KEY, TARGET_LOCATION)
            if not bounds:
                print("Failed to get bounding box. Exiting.")
                return
        
        print(f"Search area bounds: {bounds}")
        
        # Generate the initial grid points
        grid_points = generate_grid_points(bounds, INITIAL_GRID_STEP)
        
        # Track points for visualization
        processed_grid_points = []
        refinement_points = []
        
        # Statistics
        total_places_found = len(all_place_ids)
        points_processed = 0
        refinements_triggered = 0
        start_time = time.time()
        
        # Open files for appending
        with open(REFINEMENT_LOG, 'a') as refinement_log:
            try:
                # Process each grid point
                for i, point_coords in enumerate(grid_points):
                    lat, lng = point_coords
                    
                    # Check for API call limit
                    if args.max_calls > 0 and GLOBAL_API_CALLS >= args.max_calls:
                        print(f"Reached maximum API call limit of {args.max_calls}. Stopping.")
                        break
                    
                    # Skip if already completed
                    if (lat, lng, "standard") in completed_points:
                        print(f"Skipping already processed point {i+1}/{len(grid_points)}: ({lat:.6f}, {lng:.6f})")
                        continue
                    
                    # Check if point is in refining state
                    in_refining_state = (lat, lng, "standard") in refining_points
                    
                    print(f"\n--- Processing Point {i+1}/{len(grid_points)}: ({lat:.6f}, {lng:.6f}) ---")
                    
                    try:
                        # If this was a point in refining state, we only need to handle refinement
                        if not in_refining_state:
                            # Perform the initial search at this point
                            api_calls, results_count, threshold_exceeded, place_ids = perform_search_at_point(
                                point_coords, INITIAL_RADIUS, all_place_ids, OUTPUT_FILE
                            )
                            
                            points_processed += 1
                            
                            # Track for visualization
                            processed_grid_points.append(point_coords)
                            
                            print(f"Found {results_count} results in total. {len(place_ids)} unique IDs for this point.")
                            
                            # *** ADD NEAR-LIMIT LOGGING ***
                            if results_count >= 58:  # Log if very close to the ~60 limit
                                print(f"   *** WARNING: Point ({lat:.6f}, {lng:.6f}) returned {results_count} results - potential API truncation. Logging.")
                                with open(NEAR_LIMIT_LOG, 'a') as near_limit_log:
                                    near_limit_log.write(f"{lat},{lng},{results_count},{INITIAL_RADIUS}\n")
                            # *** END ADDITION ***
                        else:
                            # We're resuming a point that was interrupted during refinement
                            print(f"Resuming refinement for this point.")
                            threshold_exceeded = True
                            results_count = SUBDIVISION_THRESHOLD  # Assume it exceeded threshold since it was in refining state
                        
                        # Check if we need to perform adaptive refinement
                        if threshold_exceeded:
                            print(f"*** THRESHOLD EXCEEDED: {results_count} results at ({lat:.6f}, {lng:.6f}) ***")
                            print(f"Initiating adaptive refinement.")
                            
                            # Log the refinement
                            if not in_refining_state:  # Only log if not already in refinement state
                                refinement_log.write(f"{lat},{lng},{results_count},{INITIAL_RADIUS}\n")
                                refinement_log.flush()
                                
                                # Mark this point as being in refinement
                                save_progress_point(point_coords, "standard", POINT_STATE_REFINING, PROGRESS_FILE)
                            
                            # Calculate parameters for the refined search
                            mini_radius = INITIAL_RADIUS / MINI_RADIUS_FACTOR
                            mini_step = mini_radius * MINI_GRID_OVERLAP_FACTOR
                            
                            print(f"Refining with radius: {mini_radius:.1f}m, step: {mini_step:.1f}m")
                            
                            # Generate the mini-grid points
                            mini_grid_points = generate_mini_grid(point_coords, INITIAL_RADIUS, mini_step)
                            
                            print(f"Generated {len(mini_grid_points)} mini-grid points for refinement.")
                            
                            # Process each mini-grid point
                            mini_grid_api_calls = 0
                            for j, mini_point in enumerate(mini_grid_points):
                                mini_lat, mini_lng = mini_point
                                
                                # Check for API call limit
                                if args.max_calls > 0 and GLOBAL_API_CALLS >= args.max_calls:
                                    print(f"Reached maximum API call limit during refinement. Stopping.")
                                    raise Exception("API call limit reached during refinement")
                                
                                # Skip if already processed
                                if (mini_lat, mini_lng, "mini") in completed_points:
                                    print(f"   Skipping already processed mini-point {j+1}/{len(mini_grid_points)}")
                                    continue
                                
                                # OPTIMIZATION: Skip if too close to another already processed mini-point
                                proximity_threshold = mini_radius * 0.5  # Half the mini radius
                                skip_point = False
                                
                                for searched_lat, searched_lng in searched_mini_areas:
                                    if haversine_distance(mini_lat, mini_lng, searched_lat, searched_lng) < proximity_threshold:
                                        print(f"   Skipping mini-point {j+1}/{len(mini_grid_points)} - too close to previously searched area")
                                        skip_point = True
                                        break
                                        
                                if skip_point:
                                    continue
                                
                                print(f"   Processing mini-point {j+1}/{len(mini_grid_points)}: ({mini_lat:.6f}, {mini_lng:.6f})")
                                
                                # Perform the search at this mini-point
                                calls_made = perform_refined_search_at_point(mini_point, mini_radius, all_place_ids, OUTPUT_FILE)
                                
                                mini_grid_api_calls += calls_made
                                
                                # Save progress for this mini-point
                                save_progress_point(mini_point, "mini", POINT_STATE_COMPLETE, PROGRESS_FILE)
                                
                                # Add to searched areas for overlap mitigation
                                searched_mini_areas.add((mini_lat, mini_lng))
                                
                                # Track for visualization
                                refinement_points.append(mini_point)
                                
                                # Small delay between mini-grid calls
                                time.sleep(0.5)
                            
                            print(f"*** Refinement complete. Made {mini_grid_api_calls} additional API calls.")
                            refinements_triggered += 1
                            
                            # Mark standard point as complete now that refinement is done
                            save_progress_point(point_coords, "standard", POINT_STATE_COMPLETE, PROGRESS_FILE)
                        else:
                            # No refinement needed, mark as complete
                            save_progress_point(point_coords, "standard", POINT_STATE_COMPLETE, PROGRESS_FILE)
                    
                    except Exception as e:
                        print(f"Error processing point {point_coords}: {e}")
                        print("Continuing with next point...")
                        continue
                    
                    # Print current statistics
                    elapsed = time.time() - start_time
                    print(f"\nCurrent Statistics:")
                    print(f"  - Runtime: {elapsed:.1f} seconds")
                    print(f"  - Points Processed: {points_processed}/{len(grid_points)}")
                    print(f"  - API Calls: {GLOBAL_API_CALLS}")
                    print(f"  - Unique Place IDs: {len(all_place_ids)}")
                    print(f"  - Refinements Triggered: {refinements_triggered}")
                    
                    # Be nice to Google's API - wait between grid points
                    time.sleep(1)
                    
            except Exception as e:
                print(f"\n*** ERROR IN MAIN PROCESSING LOOP: {e} ***")
                print("Attempting to continue with final reporting...")
        
        # Generate visualization if requested and we have data
        if args.visualize and (processed_grid_points or place_ids_with_coords):
            try:
                visualize_search_results(
                    processed_grid_points, 
                    refinement_points, 
                    place_ids_with_coords,
                    VISUALIZATION_FILE
                )
                print(f"Visualization saved to {VISUALIZATION_FILE}")
                
                # Save map data for future combination
                save_map_data(
                    processed_grid_points,
                    refinement_points,
                    place_ids_with_coords,
                    MAP_DATA_FILE
                )
                print(f"Map data saved to {MAP_DATA_FILE}")
            except Exception as viz_error:
                print(f"Failed to generate visualization: {viz_error}")
        
        # Final report
        elapsed = time.time() - start_time
        print("\n\n--- Extraction Complete ---")
        print(f"Final Statistics:")
        print(f"  - Runtime: {elapsed:.1f} seconds")
        print(f"  - Points Processed: {points_processed}/{len(grid_points)}")
        print(f"  - API Calls: {GLOBAL_API_CALLS}")
        print(f"  - Unique Place IDs: {len(all_place_ids)}")
        print(f"  - Refinements Triggered: {refinements_triggered}")
        print(f"\nResults saved to {OUTPUT_FILE}")
        
        # Create CSV summary of all places
        csv_file = create_summary_csv(
            target_location=TARGET_LOCATION if not args.test_area else TEST_AREAS.get(args.test_area, {}).get("name", ""),
            mode=mode_slug
        )
        if csv_file:
            print(f"CSV summary saved to {csv_file}")
        
    except Exception as main_error:
        print(f"\n*** CRITICAL ERROR: {main_error} ***")
        print("Script execution failed. Check logs for details.")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()