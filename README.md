# Google Maps Place Search Tool

A Python-based tool for extracting place data from Google Maps using the Google Places API. The tool employs an adaptive grid-based approach to efficiently find and extract data about any type of establishment in a geographic area.

## Features

- Performs efficient geospatial searching using adaptive grid refinement
- Automatically handles Google Maps API pagination and result limitations
- Works with any place type supported by Google Places API (restaurants, hotels, gyms, etc.)
- Extracts detailed business information:
  - Business name and place ID
  - Address and coordinates
  - Rating and total number of ratings
  - Business status
  - Types and categories
  - Phone number and website
  - Opening hours
  - Reviews (when available)
- Handles API rate limiting with exponential backoff
- Visualizes search coverage and results on interactive maps
- Maintains progress and can resume interrupted searches
- Exports data to CSV for further analysis

## Key Components

- **physio-search**: Main search tool using the Google Places API (can be renamed to reflect its generic nature)
- **get_details.py**: Utility to fetch detailed information for collected place IDs
- **combine_maps.py**: Tool to combine visualization data from multiple searches

## Requirements

- Python 3.8+
- Google Maps API key with Places API enabled
- Required Python packages:
  - requests
  - python-dotenv
  - folium (for visualizations)

## Installation

1. Clone this repository:
   ```
   git clone https://github.com/username/google-maps-place-search.git
   cd google-maps-place-search
   ```

2. Create and activate a virtual environment:
   ```
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. Install the required packages:
   ```
   pip install requests python-dotenv folium
   ```

4. Create a `.env` file with your Google Maps API key:
   ```
   GOOGLE_MAPS_API_KEY=your_api_key_here
   ```

## Usage

### Basic Search

Run the main search script to find place IDs:

```
python physio-search
```

By default, this will search for physiotherapists in Berlin, Germany, but you can modify the settings as explained below.

### Command Line Options

The search script supports several command-line options:

```
python physio-search [options]
```

Options:
- `--dry-run`: Run in mock mode without making actual API calls
- `--test-area {alexanderplatz,tiergarten,kreuzberg,friedrichstrasse,all}`: Run on a specific test area
- `--max-calls N`: Maximum API calls to make before stopping (0 = unlimited)
- `--visualize`: Generate visualization maps of the search
- `--param-test`: Run parameter sensitivity testing
- `--combine-maps MAP1 MAP2 [...]`: Combine multiple saved map data files into one visualization

### Fetching Detailed Information

After collecting place IDs, use get_details.py to fetch comprehensive details:

```
python get_details.py place_ids_output_file.txt
```

This will create a CSV file with detailed information about each place.

## Customizing for Different Searches

To modify the script for searching different place types or locations, edit these variables near the top of the `physio-search` script:

```python
# Change these values to customize your search
TARGET_LOCATION = "Berlin, Germany"  # Any location Google Maps recognizes
PLACE_TYPE = "physiotherapist"  # Any place type from Google Places API
```

### Supported Place Types

Google Places API supports many place types, including:
- restaurant, cafe, bar
- hospital, doctor, pharmacy
- gym, spa, beauty_salon
- school, university, library
- store, supermarket, shopping_mall
- hotel, lodging, rv_park
- airport, train_station, bus_station
- and many more

See the [Google Places API documentation](https://developers.google.com/maps/documentation/places/web-service/supported_types) for a complete list of supported place types.

### Adjusting Search Parameters

You can also modify these parameters to adjust how the search works:

```python
# Search intensity parameters
MAX_RADIUS = 5000  # Maximum allowed radius in meters (5km)
INITIAL_RADIUS = 750  # Initial search radius for standard grid points
INITIAL_GRID_STEP = 750  # Distance between standard grid points

# Refinement parameters
SUBDIVISION_THRESHOLD = 45  # When to subdivide into a finer grid
MINI_RADIUS_FACTOR = 3.0  # mini_radius = original_radius / MINI_RADIUS_FACTOR
MINI_GRID_OVERLAP_FACTOR = 1.0  # Affects density of the mini grid
```

- Increase `INITIAL_RADIUS` and `INITIAL_GRID_STEP` for faster searches of sparse areas
- Decrease these values for more thorough searches of dense areas
- Adjust `SUBDIVISION_THRESHOLD` to control when the script creates a finer search grid
- Modify `API_DELAY_SECONDS` to control the delay between API calls

## How It Works

1. **Grid-Based Approach**: Divides the search area into a grid of points.
2. **Adaptive Refinement**: When a high concentration of results is found, creates a finer grid in that area.
3. **Progressive Searching**: Processes each grid point, tracking progress to allow resuming interrupted searches.
4. **Visualization**: Generates maps showing the search pattern and found locations.
5. **Data Collection**: Saves place IDs during search, then fetches detailed information in a second pass.

## Files Generated

- `place_ids_*.txt`: List of unique place IDs found during search
- `progress_*.txt`: Search progress tracking for resumable operation
- `refinements_*.txt`: Log of areas requiring refinement
- `map_*.html`: Visualization of the search coverage and results
- `place_details_summary_*.csv`: Detailed information about each place

## Notes

- The script respects Google's API rate limits and employs back-off strategies.
- API usage costs are associated with the Google Places API - check Google's pricing.
- For large areas, the search might take significant time and API calls.
- The file and directory naming will reflect your chosen place type and location.

## License

MIT

## Disclaimer

This tool is for educational purposes only. Users are responsible for complying with Google's Terms of Service and API usage policies. 