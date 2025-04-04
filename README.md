# Google Maps Scraper

A Python-based scraper for extracting information and reviews about physiotherapists from Google Maps.

## Features

- Searches for physiotherapists in specified locations
- Extracts detailed business information:
  - Business name
  - Address
  - Place ID
  - Coordinates
  - Phone number
  - Website
  - Doctolib booking URL (if available)
  - Overall rating and total number of ratings
- Collects reviews with:
  - Review author
  - Rating
  - Review text (full text with "More" button support)
  - Time of review
  - Detected language
- Handles Google Maps UI including:
  - Cookie consent dialogs
  - Business listings and search results
  - Review tab navigation
  - Review pagination and "Show more" functionality

## Requirements

- Python 3.8+
- Playwright
- Additional dependencies in `requirements.txt`

## Installation

1. Clone this repository:
   ```
   git clone https://github.com/adriansprk/google-maps-scraper.git
   cd google-maps-scraper
   ```

2. Create and activate a virtual environment:
   ```
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install the required packages:
   ```
   pip install -r requirements.txt
   ```

4. Install Playwright browsers:
   ```
   playwright install chromium
   ```

## Usage

1. Add your search queries to `input.txt`, one per line. For example:
   ```
   Physiotherapeut Berlin
   Physiotherapeut Hamburg
   ```

2. Run the script:
   ```
   python maps_scraper.py
   ```

3. Results will be saved in the `results` directory as CSV files.

## Configuration

You can customize the scraper by modifying these constants in `maps_scraper.py`:

- `MAX_PLACES`: Number of businesses to scrape per search query
- `MAX_REVIEWS_PER_PLACE`: Maximum number of reviews to extract per business
- `SCROLL_PAUSE_TIME`: Time to pause between scrolls
- `USE_HEADLESS`: Set to True for production, False for debugging (to see the browser)

## Notes

- The script respects Google's robots.txt and includes appropriate delays between actions.
- For debugging purposes, the script takes screenshots at various stages.
- All results are timestamped and organized in the results directory.

## License

MIT License

## Disclaimer

This tool is for educational purposes only. Be sure to comply with Google's Terms of Service when using this scraper. 