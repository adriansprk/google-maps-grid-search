#!/usr/bin/env python3
"""
Google Maps Reviews Scraper for Physiotherapists in Berlin
---------------------------------------------------------
This script uses Playwright to scrape reviews from Google Maps for physiotherapists in Berlin.
It extracts place information and reviews, detects languages, and saves the data to a CSV file.
"""

import asyncio
import csv
import json
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any, Set
import traceback

import langdetect
from langdetect import DetectorFactory
from playwright.async_api import async_playwright, Page, ElementHandle, Locator
from tqdm import tqdm

# Set seed for language detection to ensure consistent results
DetectorFactory.seed = 0

# Constants
SEARCH_QUERIES = [
    "Physiotherapeut Berlin",
    # Add more search queries here
]
MAX_PLACES = 2  # Set to 3 places now that our code is more robust
MAX_REVIEWS_PER_PLACE = 500  # Increased from 200 to get more reviews with our improved extraction
SCROLL_PAUSE_TIME = 1.5  # Time to pause between scrolls in seconds
CSV_FILENAME = "physiotherapists_berlin_playwright.csv"
LOAD_TIMEOUT = 30000  # Increase page load timeout to 30 seconds
COOKIE_BANNER_TIMEOUT = 5000  # Cookie banner timeout in milliseconds
MAX_RETRIES = 3  # Maximum number of retries for critical operations
USE_HEADLESS = False  # Set to True for production, False for debugging


def extract_coordinates_from_url(url: str) -> Tuple[float, float]:
    """Extract latitude and longitude from Google Maps URL."""
    try:
        if "/@" in url:
            coordinates = url.split('/@')[-1].split('/')[0]
            parts = coordinates.split(',')
            if len(parts) >= 2:
                return float(parts[0]), float(parts[1])
    except Exception as e:
        print(f"Error extracting coordinates: {e}")
    return 0.0, 0.0


async def accept_cookies(page: Page) -> None:
    """Accept cookies on Google Maps."""
    try:
        print("Looking for cookie consent dialog...")
        
        # First check if consent dialog is visible using specific selectors
        dialog_visible = False
        dialog_selectors = [
            'div.VtwTSb',                     # Main consent dialog container
            'form[action^="https://consent.google.com"]',  # Consent form
            'div.azc5Yc',                     # Dialog region
            'div.KGghXd'                      # Dialog overlay
        ]
        
        for selector in dialog_selectors:
            try:
                dialog = await page.query_selector(selector)
                if dialog:
                    dialog_visible = True
                    print(f"Found cookie dialog with selector: {selector}")
                    break
            except:
                continue
        
        if not dialog_visible:
            print("No cookie consent dialog detected.")
            return
        
        # Look specifically for the "Accept all" button by JSName
        print("Looking for the 'Accept all' button...")
        try:
            # Specific target using jsname attribute (from user's HTML)
            accept_button = await page.query_selector('button[jsname="b3VHJd"], button[jsname="higCR"]')
            if accept_button:
                print("Found the 'Accept all' button by jsname attribute, clicking it...")
                await accept_button.click()
                print("Clicked the consent button.")
                await asyncio.sleep(1)
                return
        except Exception as e:
            print(f"Error clicking specific jsname button: {e}")
        
        # Try by text content if jsname approach fails
        try:
            button_texts = [
                "Alle akzeptieren",
                "Accept all",
                "I agree",
                "Agree to all",
                "Agree",
                "Akzeptieren",
                "Ich stimme zu"
            ]
            
            for text in button_texts:
                button = await page.query_selector(f'button:text-is("{text}")')
                if button:
                    print(f"Found button with text '{text}', clicking it...")
                    await button.click()
                    print("Clicked the consent button by text.")
                    await asyncio.sleep(1)
                    return
        except Exception as e:
            print(f"Error clicking button by text: {e}")
        
        # Try other button selectors as fallback
        cookie_button_selectors = [
            'form[action^="https://consent.google.com"] button + button',  # The second button in the form
            'div.VtwTSb button + button',  # Second button in the dialog
            'button.tHlp8d',
            'button:has-text("Accept")',
            'button:has-text("Akzeptieren")',
            'div[role="dialog"] button:nth-of-type(2)'  # Often the second button is accept
        ]
        
        for selector in cookie_button_selectors:
            try:
                button = await page.query_selector(selector)
                if button:
                    print(f"Found fallback button with selector '{selector}', clicking it...")
                    await button.click()
                    print("Clicked fallback consent button.")
                    await asyncio.sleep(1)
                    return
            except Exception as e:
                print(f"Error clicking fallback button {selector}: {e}")
        
        # Take a screenshot if no buttons could be clicked
        print("All cookie consent button attempts failed. Taking a screenshot...")
        await page.screenshot(path="cookie_consent_failed.png")
        
    except Exception as e:
        print(f"Exception in cookie dialog handling: {e}")
        await page.screenshot(path="cookie_error.png")


async def search_google_maps(page: Page, query: str) -> None:
    """Navigate to Google Maps and search for the query."""
    try:
        print(f"Navigating to Google Maps...")
        await page.goto("https://www.google.com/maps", timeout=LOAD_TIMEOUT)
        
        # Wait for the page to fully load
        await page.wait_for_load_state("networkidle", timeout=LOAD_TIMEOUT)
        
        # Take a screenshot after navigation
        await page.screenshot(path="maps_initial_page.png")
        
        # Handle cookies if needed
        await accept_cookies(page)
        
        # Add an extra wait time before searching
        print("Waiting for page to stabilize before searching...")
        await asyncio.sleep(3)
        
        # Try different search input selectors
        search_input_selectors = [
            'input[name="q"]',
            'input[aria-label="Search Google Maps"]',
            'input[aria-label="Nach Google Maps suchen"]',
            'input[placeholder="Search Google Maps"]',
            'input[placeholder="Nach Google Maps suchen"]',
            'input#searchboxinput',
            '#searchboxinput',
            'input.searchboxinput',
            'input.tactile-searchbox-input'
        ]
        
        input_found = False
        search_input = None
        
        # First try to find the input element
        for selector in search_input_selectors:
            try:
                input_element = page.locator(selector)
                count = await input_element.count()
                if count > 0:
                    print(f"Found search input with selector: {selector}")
                    search_input = input_element
                    input_found = True
                    break
            except Exception as e:
                print(f"Error with search input selector {selector}: {e}")
                continue
        
        if not input_found:
            # Try one more approach - look for the search box container and then find the input inside it
            try:
                search_box = await page.query_selector('div.searchbox')
                if search_box:
                    input_inside = await search_box.query_selector('input')
                    if input_inside:
                        print("Found search input inside searchbox div")
                        await input_inside.fill(query)
                        await asyncio.sleep(1)
                        await input_inside.press("Enter")
                        print(f"Submitted search for: {query}")
                        input_found = True
            except Exception as e:
                print(f"Error finding input inside searchbox: {e}")
        
        if not input_found:
            # Take screenshots to help diagnose the issue
            print("Could not find search input. Taking screenshot for debugging...")
            await page.screenshot(path="search_input_not_found.png")
            
            # Try JavaScript approach as last resort
            try:
                result = await page.evaluate(f"""() => {{
                    // Look for any input that might be the search box
                    const inputs = Array.from(document.querySelectorAll('input'));
                    for (const input of inputs) {{
                        if (input.id === 'searchboxinput' || 
                            input.name === 'q' ||
                            input.placeholder?.includes('Search') ||
                            input.placeholder?.includes('suchen') ||
                            input.getAttribute('aria-label')?.includes('Search') ||
                            input.getAttribute('aria-label')?.includes('suchen')) {{
                            
                            input.value = "{query}";
                            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            
                            // Wait a bit and then try to dispatch Enter
                            setTimeout(() => {{
                                input.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }}));
                            }}, 500);
                            
                            return true;
                        }}
                    }}
                    return false;
                }}""")
                
                if result:
                    print("Used JavaScript to fill and submit search")
                    input_found = True
                    await asyncio.sleep(2)  # Wait for the JavaScript-triggered search to complete
                else:
                    raise Exception("No search input found on Google Maps even with JavaScript approach")
            except Exception as js_error:
                print(f"JavaScript approach failed: {js_error}")
                raise Exception("No search input found on Google Maps")
        elif search_input:
            # If we found the input element properly, use it
            try:
                # Clear existing text first
                await search_input.click()
                await search_input.fill("")
                await asyncio.sleep(0.5)
                
                # Enter the search query
                await search_input.fill(query)
                await asyncio.sleep(1)  # Short pause before pressing Enter
                
                # Try hitting Enter
                await search_input.press("Enter")
                print(f"Submitted search for: {query}")
            except Exception as e:
                print(f"Error filling search input: {e}")
                raise
        
        # Wait for search results to load
        print("Waiting for search results to load...")
        
        # Try different feed selectors
        feed_selectors = [
            'div[role="feed"]',
            'div.section-result-content',
            'div.section-layout',
            'div#search-feed',
            'div.m6QErb.DxyBCb.kA9KIf.dS8AEf',
            'div.m6QErb',
            'div[role="main"] div[role="feed"]',
            'div[jsaction*="placeresult"]'
        ]
        
        feed_found = False
        for selector in feed_selectors:
            try:
                # Wait for the feed with a reasonable timeout
                await page.wait_for_selector(selector, timeout=LOAD_TIMEOUT // 2)
                print(f"Found results feed with selector: {selector}")
                feed_found = True
                break
            except:
                pass
        
        if not feed_found:
            # If we couldn't find a feed element, check for specific elements that indicate results
            print("Could not identify results feed. Checking for place results...")
            
            place_result_selectors = [
                "a[href*='/maps/place/']",
                "div.Nv2PK",
                "a.hfpxzc",
                "div[jsaction*='pane.placeResult']",
                "div[role='article']"
            ]
            
            results_found = False
            for selector in place_result_selectors:
                try:
                    result_count = await page.locator(selector).count()
                    if result_count > 0:
                        print(f"Found {result_count} search results with selector: {selector}")
                        results_found = True
                        break
                except:
                    pass
            
            if not results_found:
                # Wait for general page load and take a screenshot
                await page.wait_for_load_state("networkidle", timeout=LOAD_TIMEOUT)
                await page.screenshot(path="search_results_no_items.png")
                print("No search results detected, but page loaded")
            else:
                print("Results found using alternative selectors")
        
        # Take a screenshot after results are loaded
        await page.screenshot(path="search_results_loaded.png")
        
        # Give extra time for results to fully load
        await asyncio.sleep(5)
        print("Search results page loaded")
        
    except Exception as e:
        print(f"Error during search: {e}")
        # Take a screenshot to help debug
        try:
            await page.screenshot(path="search_error.png")
        except:
            print("Could not take error screenshot")
        raise  # Re-raise the exception to be handled by the caller


async def extract_place_ids(page: Page, results_dir: str, query_index: int, max_places: int = MAX_PLACES) -> list:
    """Extract place IDs from the search results page."""
    place_ids = set()  # Use a set to avoid duplicates
    
    try:
        print("Waiting for search results to load...")
        # Wait for results to be visible
        try:
            # First try to wait for the results feed (fix the CSS selector syntax)
            await page.wait_for_selector("div[role='feed'], div.m6QErb.DxyBCb.kA9KIf.dS8AEf", timeout=LOAD_TIMEOUT)
            print("Search results feed found")
        except Exception as e:
            print(f"Error waiting for results feed: {e}")
            print("Trying to look for result items directly")
            try:
                await page.wait_for_selector("a[href*='/maps/place/']", timeout=LOAD_TIMEOUT)
                print("Found some place links directly")
            except Exception as e2:
                print(f"Error finding place links: {e2}")
                # Last resort - wait for any content to load
                await page.wait_for_load_state("networkidle", timeout=LOAD_TIMEOUT)
                print("Waited for network idle state")
    
        # Wait a bit for JavaScript to finish rendering
        await asyncio.sleep(5)
        
        # Take a screenshot of search results
        await page.screenshot(path=f"{results_dir}/before_extraction_{query_index+1}.png")
        
        # Check if this is already a single business result page
        current_url = page.url
        if '/maps/place/' in current_url:
            print("We're already on a place page, extracting ID from URL...")
            # Extract place ID from URL
            match = re.search(r'/maps/place/[^/]+/([^/\?]+)', current_url)
            if match and match.group(1):
                place_id = match.group(1)
                if not place_id.startswith('data='):
                    place_ids.add(place_id)
                    print(f"Extracted place ID from URL: {place_id}")
                    return list(place_ids)  # Return early since we're already on a place page
        
        print("Scrolling to load more results...")
        # Reduced scrolling to avoid rate limiting - just 2 scrolls maximum
        scrolls_needed = 2
        for i in range(scrolls_needed):
            # Find the feed container or result container
            containers = [
                "div[role='feed']",
                "div.m6QErb.DxyBCb.kA9KIf.dS8AEf",
                "div.m6QErb",
                "div.section-layout",
                "div.section-result-content"
            ]
            
            container = None
            for selector in containers:
                try:
                    container = await page.query_selector(selector)
                    if container:
                        break
                except:
                    continue
            
            if container:
                try:
                    # Scroll the container with a smaller scroll distance
                    await container.evaluate("""(container) => {
                        container.scrollBy(0, 500);
                    }""")
                    print(f"Scrolled down ({i+1}/{scrolls_needed})")
                except Exception as e:
                    print(f"Error scrolling container: {e}")
            else:
                # Fallback: scroll the page with a smaller scroll distance
                await page.evaluate("window.scrollBy(0, 500)")
                print(f"Scrolled page down ({i+1}/{scrolls_needed})")
            
            # Wait longer for content to load
            await asyncio.sleep(3)
        
        # Take a screenshot after scrolling
        await page.screenshot(path=f"{results_dir}/after_scrolling_{query_index+1}.png")
        
        # First try to extract place IDs without clicking to avoid potential issues
        print("Extracting place IDs from the page...")
        
        # Method 1: Try to extract place IDs from href attributes
        try:
            links = await page.query_selector_all("a[href*='/maps/place/']")
            print(f"Found {len(links)} place links")
            
            for link in links:
                try:
                    href = await link.get_attribute('href')
                    if href and '/maps/place/' in href:
                        # Extract place ID from URL
                        match = re.search(r'/maps/place/[^/]+/([^/\?]+)', href)
                        if match and match.group(1):
                            place_id = match.group(1)
                            if place_id not in place_ids and not place_id.startswith('data='):
                                place_ids.add(place_id)
                                print(f"Extracted place ID from href: {place_id}")
                                
                                # Break if we have enough place IDs
                                if len(place_ids) >= max_places:
                                    break
                except Exception as e:
                    print(f"Error extracting from link: {e}")
        except Exception as e:
            print(f"Error finding links: {e}")
        
        # Try to get more place IDs without clicking
        if len(place_ids) < max_places:
            try:
                # Try data-item-id attributes which may contain place IDs
                item_elements = await page.query_selector_all("[data-item-id]")
                print(f"Found {len(item_elements)} elements with data-item-id")
                
                for element in item_elements:
                    try:
                        item_id = await element.get_attribute('data-item-id')
                        if item_id and ':' in item_id:
                            # Format might be like "0:abcdef12345..."
                            potential_place_id = item_id.split(':')[-1]
                            if len(potential_place_id) > 10 and potential_place_id not in place_ids:
                                place_ids.add(potential_place_id)
                                print(f"Extracted place ID from data-item-id: {potential_place_id}")
                                
                                if len(place_ids) >= max_places:
                                    break
                    except:
                        continue
            except Exception as e:
                print(f"Error extracting from data-item-id: {e}")
        
        # If we don't have enough place IDs, only then try clicking
        if len(place_ids) < max_places:
            print("Not enough place IDs, will try clicking on first item to extract from URL...")
            try:
                # Find first result and click it
                result_selectors = [
                    "div.Nv2PK",
                    "a.hfpxzc",
                    "div[role='article']",
                    "a[href*='/maps/place/']"
                ]
                
                for selector in result_selectors:
                    first_result = await page.query_selector(selector)
                    if first_result:
                        print(f"Found result with selector {selector}, clicking it...")
                        await first_result.click()
                        await asyncio.sleep(2)
                        
                        # Extract place ID from URL
                        current_url = page.url
                        match = re.search(r'/maps/place/[^/]+/([^/\?]+)', current_url)
                        if match and match.group(1):
                            place_id = match.group(1)
                            if place_id not in place_ids and not place_id.startswith('data='):
                                place_ids.add(place_id)
                                print(f"Extracted place ID from clicking result: {place_id}")
                        else:
                            # Try to extract coordinates if no place ID found
                            coords_match = re.search(r'@([0-9.-]+),([0-9.-]+)', current_url)
                            if coords_match:
                                lat = coords_match.group(1)
                                lng = coords_match.group(2)
                                place_id = f"@{lat},{lng},14z"
                                if place_id not in place_ids:
                                    place_ids.add(place_id)
                                    print(f"Extracted coordinates as place ID: {place_id}")
                        
                        # Go back to results
                        await page.go_back()
                        await asyncio.sleep(3)
                        break
            except Exception as e:
                print(f"Error clicking on first result: {e}")
        
        # Final fallback: If we still have no place IDs, extract coordinates from URL
        if len(place_ids) == 0:
            print("No place IDs found, using coordinates from URL as fallback...")
            current_url = page.url
            coords_match = re.search(r'@([0-9.-]+),([0-9.-]+)', current_url)
            if coords_match:
                lat = coords_match.group(1)
                lng = coords_match.group(2)
                place_id = f"@{lat},{lng},14z"
                place_ids.add(place_id)
                print(f"Using coordinates as fallback place ID: {place_id}")
        
        # Take a final screenshot
        await page.screenshot(path=f"{results_dir}/after_extraction_{query_index+1}.png")
        
    except Exception as e:
        print(f"Error extracting place IDs: {e}")
        print("Taking a screenshot of the error state...")
        try:
            await page.screenshot(path=f"{results_dir}/place_id_extraction_error.png")
        except:
            print("Failed to take screenshot - page may be closed")
        
    return list(place_ids)


async def get_place_details(page: Page, place_id: str) -> Dict[str, str]:
    """Get details about a specific place from its ID."""
    place_info = {
        'place_id': place_id,
        'company_name': 'Not found',
        'address': 'Not found',
        'overall_rating': '0',
        'total_ratings': '0',
    }
    
    try:
        print(f"Navigating to place details for {place_id}...")
        
        # Handle different format of place_id
        url = ""
        if place_id.startswith('@'):
            # This is a coordinates-based ID like "@52.52661,13.3784811,14z"
            url = f"https://www.google.com/maps/place/{place_id}"
        elif place_id.startswith('http'):
            # This is already a full URL
            url = place_id
        else:
            # This is a standard place ID
            url = f"https://www.google.com/maps/place/?q=place_id:{place_id}"
        
        print(f"Opening URL: {url}")
        
        # Navigate with longer timeout and wait for network idle
        await page.goto(url, timeout=LOAD_TIMEOUT)
        await page.wait_for_load_state("networkidle", timeout=LOAD_TIMEOUT)
        
        # Wait for details to appear
        await asyncio.sleep(3)
        
        # Take a screenshot for debugging
        await page.screenshot(path=f"place_details_{place_id[:8].replace(':', '_').replace('@', 'at_')}.png")
        
        # Try to extract business name from the title
        try:
            page_title = await page.title()
            if page_title and " - Google Maps" in page_title:
                business_name = page_title.split(" - Google Maps")[0].strip()
                if business_name and len(business_name) > 3:  # Minimum length check
                    place_info['company_name'] = business_name
                    print(f"Extracted company name from title: {business_name}")
        except Exception as e:
            print(f"Error extracting title: {e}")
        
        # If title approach failed, try the DOM elements
        if place_info['company_name'] == 'Not found':
            # Get company name with retries
            retries = 3
            for attempt in range(retries):
                try:
                    # Try different selectors for company name
                    name_selectors = [
                        'h1.DUwDvf', 
                        'h1[data-test="hero-title"]',
                        'div.fontHeadlineLarge',
                        'h1.fontHeadlineLarge',
                        'div.tAiQdd > div.lMbq3e > div:nth-child(1) > h1',
                        'div[role="main"] h1',
                        'div[data-attrid="title"]',
                        'span.DUwDvf',
                        'span.fontHeadlineLarge'
                    ]
                    
                    for selector in name_selectors:
                        name_element = page.locator(selector).first
                        if await name_element.count() > 0:
                            company_name = await name_element.text_content()
                            if company_name and len(company_name.strip()) > 0:
                                place_info['company_name'] = company_name.strip()
                                print(f"Found company name: {place_info['company_name']}")
                                break
                    
                    if place_info['company_name'] != 'Not found':
                        break
                    
                    print(f"Company name not found on attempt {attempt+1}, retrying...")
                    await asyncio.sleep(2)
                except Exception as e:
                    print(f"Error getting company name (attempt {attempt+1}): {e}")
                    await asyncio.sleep(2)
        
        # Get address
        try:
            # Try different selectors for address
            address_selectors = [
                'button[data-item-id="address"]',
                'button[aria-label*="Adresse"]',
                'button[aria-label*="address"]',
                'button[data-tooltip="Adresse kopieren"]',
                'button[data-tooltip="Copy address"]',
                'div[data-attrid="address"]',
                'span[jsan*="address"]',
                'button[jstcache]'
            ]
            
            for selector in address_selectors:
                address_element = page.locator(selector).first
                if await address_element.count() > 0:
                    address = await address_element.text_content()
                    if address and len(address.strip()) > 0:
                        place_info['address'] = address.strip()
                        print(f"Found address: {place_info['address']}")
                        break
            
            # If we didn't find an address, try a more general approach
            if place_info['address'] == 'Not found':
                # Look for elements that might contain address
                potential_address_elements = await page.query_selector_all('button, span, div')
                for element in potential_address_elements:
                    try:
                        text = await element.text_content()
                        if text and "Berlin" in text and len(text) > 10 and len(text) < 100:
                            # This might be an address
                            place_info['address'] = text.strip()
                            print(f"Found potential address: {place_info['address']}")
                            break
                    except:
                        continue
        except Exception as e:
            print(f"Error getting address: {e}")
        
        # Get rating information
        try:
            # Try different selectors for ratings
            rating_selectors = [
                'div.F7nice',
                'span.F7nice',
                'span[aria-hidden="true"][role="img"]',
                'span.ceNzKf',
                'span[class*="stars"]',
                'div[data-attrid="star_rating"]'
            ]
            
            for selector in rating_selectors:
                rating_element = page.locator(selector).first
                if await rating_element.count() > 0:
                    rating_text = await rating_element.text_content()
                    if rating_text:
                        # Match patterns like "4.5" or "4,5"
                        rating_match = re.search(r'([0-9]+[.,][0-9]+|[0-9]+)', rating_text)
                        if rating_match:
                            place_info['overall_rating'] = rating_match.group(1).replace(',', '.')
                            
                            # Try to extract total ratings number
                            total_ratings_match = re.search(r'\(([0-9.,]+)\)', rating_text)
                            if total_ratings_match:
                                place_info['total_ratings'] = total_ratings_match.group(1).replace('.', '').replace(',', '')
                            break
            
            # If we didn't find a rating, try to extract from review button text
            if place_info['overall_rating'] == '0':
                review_buttons = await page.query_selector_all('button')
                for button in review_buttons:
                    try:
                        button_text = await button.text_content()
                        if button_text and ("review" in button_text.lower() or "rezension" in button_text.lower()):
                            # Look for patterns like "4.5 stars (123 reviews)"
                            rating_match = re.search(r'([0-9]+[.,][0-9]+|[0-9]+)\s*(?:stars|star|\★)', button_text)
                            if rating_match:
                                place_info['overall_rating'] = rating_match.group(1).replace(',', '.')
                                
                                # Look for number of reviews
                                count_match = re.search(r'\(([0-9.,]+)\)', button_text)
                                if count_match:
                                    place_info['total_ratings'] = count_match.group(1).replace('.', '').replace(',', '')
                                break
                    except:
                        continue
        except Exception as e:
            print(f"Error getting rating: {e}")
        
        # Extract place ID from URL if we got a coordinate-based ID before
        if place_id.startswith('@'):
            try:
                current_url = page.url
                id_match = re.search(r'place/[^/]+/([^/\?]+)', current_url)
                if id_match and id_match.group(1):
                    real_place_id = id_match.group(1)
                    if real_place_id != place_id:
                        print(f"Updated place ID from coordinate-based to: {real_place_id}")
                        place_info['place_id'] = real_place_id
            except:
                pass
    
    except Exception as e:
        print(f"Error getting place details: {e}")
    
    return place_info


async def navigate_to_reviews(page: Page) -> None:
    """Navigate to the reviews section of a place."""
    try:
        print("Navigating to reviews section...")
        
        # First check if we're already in the reviews section
        review_section_selectors = [
            'div[role="tablist"]',
            'div.m6QErb.DxyBCb.kA9KIf.dS8AEf',
            'div.jANrlb',
            'div.DUwDvf.lfPIob'
        ]
        
        for selector in review_section_selectors:
            review_section = await page.query_selector(selector)
            if review_section:
                review_text = await review_section.text_content()
                if review_text and any(word in review_text.lower() for word in ["review", "rezension", "bewertung"]):
                    print("Already in the reviews section")
                    return
        
        # Try clicking on reviews tab/section
        review_tab_selectors = [
            'button[aria-label*="review"], button[aria-label*="Rezension"], button[aria-label*="Bewertung"]',
            'button:has-text("Reviews"), button:has-text("Rezensionen"), button:has-text("Bewertungen")',
            'a[href*="reviews"], a[href*="Rezensionen"], a[href*="Bewertungen"]',
            'div[role="tab"]:has-text("review"), div[role="tab"]:has-text("Rezension")',
            'div.fontBodyMedium:has-text("review"), div.fontBodyMedium:has-text("Rezension")'
        ]
        
        for selector in review_tab_selectors:
            try:
                review_tabs = await page.query_selector_all(selector)
                if review_tabs and len(review_tabs) > 0:
                    # Try to find the most relevant tab
                    for tab in review_tabs:
                        tab_text = await tab.text_content() or ""
                        if any(word in tab_text.lower() for word in ["review", "rezension", "bewertung"]):
                            print(f"Clicking review tab with text: {tab_text.strip()}")
                            await tab.click()
                            await asyncio.sleep(3)  # Wait for reviews to load
                            return
                    
                    # If no specific tab found, click the first one
                    if review_tabs[0]:
                        print("Clicking first review tab found")
                        await review_tabs[0].click()
                        await asyncio.sleep(3)  # Wait for reviews to load
                        return
            except Exception as e:
                print(f"Error clicking review tab with selector {selector}: {e}")
        
        # If no tab found, try clicking on the rating itself which often leads to reviews
        rating_selectors = [
            'span[aria-label*="stars"], span[aria-label*="Sterne"]',
            'button:has-text("★"), button:has-text("Sterne")',
            'span.ceNzKf',
            'div.F7nice',
            'span[aria-hidden="true"][role="img"]'
        ]
        
        for selector in rating_selectors:
            try:
                rating_elements = await page.query_selector_all(selector)
                if rating_elements and len(rating_elements) > 0:
                    print("Clicking on rating element to navigate to reviews")
                    await rating_elements[0].click()
                    await asyncio.sleep(3)  # Wait for reviews to load
                    return
            except Exception as e:
                print(f"Error clicking rating with selector {selector}: {e}")
        
        print("Could not navigate to reviews section, will try to extract reviews from current page")
    
    except Exception as e:
        print(f"Error navigating to reviews: {e}")


async def extract_reviews(page: Page, max_reviews: int) -> List[Dict[str, str]]:
    """Extract reviews from the reviews section."""
    reviews = []
    
    try:
        print(f"Extracting up to {max_reviews} reviews...")
        
        # Take a screenshot before extraction
        await page.screenshot(path="before_review_extraction.png")
        
        # Identify the review container
        review_container_selectors = [
            'div[role="feed"]', 
            'div.m6QErb.DxyBCb.kA9KIf.dS8AEf',
            'div.m6QErb',
            'div.section-layout',
            'div.section-scrollbox'
        ]
        
        # Try to find a scrollable container
        container = None
        for selector in review_container_selectors:
            try:
                container = await page.query_selector(selector)
                if container:
                    print(f"Found review container with selector: {selector}")
                    break
            except:
                continue
        
        if not container:
            print("Could not find review container, trying to find reviews directly...")
        
        # First - handle the "Most relevant" sorting menu if it exists
        # This might give us access to more reviews
        try:
            sorting_menu_selectors = [
                'button[aria-label*="Sort reviews"]',
                'button[aria-label*="Rezensionen sortieren"]',
                'button[data-value="Sort"]',
                'button.g88MCb:has-text("Most relevant")',
                'button.g88MCb:has-text("Relevanteste")'
            ]
            
            for selector in sorting_menu_selectors:
                sort_button = await page.query_selector(selector)
                if sort_button:
                    print("Found sort button, clicking it to open menu...")
                    await sort_button.click()
                    await asyncio.sleep(1)
                    
                    # Try to select "Newest" or other non-default option to ensure all reviews load
                    sorting_options = [
                        'div[role="menuitem"]:has-text("Newest")',
                        'div[role="menuitem"]:has-text("Neueste")',
                        'div[role="menuitem"]:has-text("Highest rating")',
                        'div[role="menuitem"]:has-text("Höchste Bewertung")'
                    ]
                    
                    for option_selector in sorting_options:
                        try:
                            option = await page.query_selector(option_selector)
                            if option:
                                print(f"Selecting sort option: {await option.text_content()}")
                                await option.click()
                                await asyncio.sleep(2)  # Wait for re-sorting
                                break
                        except:
                            continue
                    
                    break
        except Exception as e:
            print(f"Error handling sort menu: {e}")
        
        # Now start the progressive loading of reviews
        print("Starting progressive loading of all reviews...")
        review_count = 0
        last_review_count = 0
        loading_attempts = 0
        max_loading_attempts = 30  # Increased to handle many review load attempts
        
        while review_count < max_reviews and loading_attempts < max_loading_attempts:
            # Take screenshot periodically to debug
            if loading_attempts % 5 == 0:
                await page.screenshot(path=f"review_loading_attempt_{loading_attempts}.png")
            
            # 1. First check how many reviews we have currently
            review_items_selectors = [
                'div.jftiEf',
                'div[data-review-id]',
                'div[role="listitem"]'
            ]
            
            for selector in review_items_selectors:
                try:
                    current_items = await page.query_selector_all(selector)
                    if current_items:
                        review_count = len(current_items)
                        print(f"Currently loaded {review_count} reviews")
                        break
                except Exception as e:
                    print(f"Error counting reviews: {e}")
            
            # If we haven't found any reviews yet, continue
            if review_count == 0:
                print("No reviews found yet, continuing to load...")
            
            # 2. Look for "Show more reviews" or similar buttons
            show_more_selectors = [
                'button:has-text("Show more reviews")',
                'button:has-text("Weitere Rezensionen anzeigen")',
                'button:has-text("More reviews")',
                'button:has-text("Mehr Rezensionen")',
                'button:has-text("Load more")',
                'button:has-text("Mehr laden")',
                'button[jsaction*="pane.review"]:has-text("Mehr")' 
            ]
            
            show_more_clicked = False
            for selector in show_more_selectors:
                try:
                    show_more_button = await page.query_selector(selector)
                    if show_more_button:
                        is_visible = await show_more_button.is_visible()
                        if is_visible:
                            print(f"Found 'Show more reviews' button: {await show_more_button.text_content()}")
                            await show_more_button.click()
                            print("Clicked 'Show more reviews' button")
                            await asyncio.sleep(2)  # Wait for more reviews to load
                            show_more_clicked = True
                            break
                except Exception as e:
                    print(f"Error clicking show more button: {e}")
            
            # 3. If no "Show more" button found, try scrolling to the bottom of the container
            if not show_more_clicked:
                try:
                    if container:
                        print("Scrolling to bottom of reviews container...")
                        await container.evaluate("""
                            (container) => {
                                container.scrollTo({
                                    top: container.scrollHeight,
                                    behavior: 'smooth'
                                });
                            }
                        """)
                    else:
                        # Try to scroll in the page
                        print("Scrolling page to try to load more reviews...")
                        await page.evaluate("""
                            window.scrollTo({
                                top: document.body.scrollHeight,
                                behavior: 'smooth'
                            });
                        """)
                    
                    # Wait for content to load
                    await asyncio.sleep(2)
                except Exception as e:
                    print(f"Error scrolling: {e}")
            
            # 4. Check if we've made progress
            if review_count == last_review_count:
                loading_attempts += 1
                print(f"No new reviews loaded (attempt {loading_attempts}/{max_loading_attempts})")
                
                # If we've tried several times with no progress, try a more aggressive approach
                if loading_attempts % 3 == 0:
                    try:
                        print("Trying more aggressive scrolling approach...")
                        # Try to find and click any elements that might trigger more reviews
                        potential_triggers = [
                            'div.m6QErb',
                            'div[role="feed"]',
                            'div.section-layout', 
                            'button.boKufb',
                            'div[jscontroller]'
                        ]
                        
                        for trigger_selector in potential_triggers:
                            triggers = await page.query_selector_all(trigger_selector)
                            if triggers:
                                # Click at the bottom of the last trigger element
                                last_trigger = triggers[-1]
                                await last_trigger.click({ position: { x: 10, y: 10 } })
                                print(f"Clicked potential trigger element: {trigger_selector}")
                                await asyncio.sleep(1)
                        
                        # Try JavaScript scrolling too
                        await page.evaluate("""
                            (() => {
                                // Try different approaches to scrolling
                                // 1. Normal scroll to bottom
                                window.scrollTo(0, document.body.scrollHeight);
                                
                                // 2. Try to find review containers and scroll them
                                const containers = [
                                    ...document.querySelectorAll('div[role="feed"]'),
                                    ...document.querySelectorAll('div.m6QErb'),
                                    ...document.querySelectorAll('div.section-layout')
                                ];
                                
                                for (const container of containers) {
                                    container.scrollTo(0, container.scrollHeight);
                                }
                                
                                // 3. Try to simulate down key presses
                                for (let i = 0; i < 10; i++) {
                                    window.dispatchEvent(new KeyboardEvent('keydown', {
                                        key: 'ArrowDown',
                                        keyCode: 40,
                                        which: 40,
                                        bubbles: true
                                    }));
                                }
                            })();
                        """)
                        await asyncio.sleep(2)
                    except Exception as e:
                        print(f"Error with aggressive approach: {e}")
            else:
                # Reset loading attempts if we made progress
                loading_attempts = 0
            
            # Update the count for next comparison
            last_review_count = review_count
            
            # If we've reached max reviews, break
            if review_count >= max_reviews:
                print(f"Reached maximum number of reviews to extract ({max_reviews})")
                break
            
            # Also break if we're getting too many attempts with no progress
            if loading_attempts >= 5:
                print("Multiple attempts with no new reviews, assuming all reviews are loaded")
                break
        
        # Take a screenshot after all loading attempts
        await page.screenshot(path="after_review_loading.png")
        
        # Now try different selectors for review items
        review_item_selectors = [
            'div.jftiEf',
            'div.gws-localreviews__google-review',
            'div[data-review-id]',
            'div.WMbnJf',
            'div[role="listitem"]',
            'div.ODSEW-ShBeI'
        ]
        
        # Try each selector to find review items
        review_items = []
        for selector in review_item_selectors:
            try:
                items = await page.query_selector_all(selector)
                if items and len(items) > 0:
                    print(f"Found {len(items)} review items with selector: {selector}")
                    review_items = items
                    break
            except Exception as e:
                print(f"Error finding review items with selector {selector}: {e}")
        
        if not review_items:
            print("No review items found, taking screenshot...")
            await page.screenshot(path="no_review_items.png")
            return []
        
        # Process all the review items we found
        # Only limit if max_reviews is smaller than what we found
        if max_reviews < len(review_items):
            print(f"Limiting to {max_reviews} reviews from {len(review_items)} found")
            review_items = review_items[:max_reviews]
        else:
            print(f"Processing all {len(review_items)} reviews found")
        
        # Process each review
        for i, item in enumerate(review_items):
            try:
                review_data = {}
                
                # Extract author
                author_selectors = [
                    'div.d4r55', 
                    'span.x3AX1-LfntMc-header-title-title',
                    'div.TSUbDb',
                    'span[class*="title"]'
                ]
                
                for selector in author_selectors:
                    try:
                        author_element = await item.query_selector(selector)
                        if author_element:
                            author = await author_element.text_content()
                            if author and author.strip():
                                review_data['review_author'] = author.strip()
                                break
                    except:
                        continue
                
                if 'review_author' not in review_data:
                    review_data['review_author'] = "Unknown"
                
                # Extract rating
                rating_selectors = [
                    'span[aria-label*="stars"], span[aria-label*="Sterne"]',
                    'span[role="img"]',
                    'span.Fam1ne',
                    'span.kvMYJc'
                ]
                
                for selector in rating_selectors:
                    try:
                        rating_element = await item.query_selector(selector)
                        if rating_element:
                            # Try to get rating from aria-label attribute
                            aria_label = await rating_element.get_attribute('aria-label')
                            if aria_label:
                                # Extract number from text like "5 stars" or "4 Sterne"
                                rating_match = re.search(r'(\d+(?:\.\d+)?)', aria_label)
                                if rating_match:
                                    review_data['review_rating'] = rating_match.group(1)
                                    break
                            
                            # If aria-label approach failed, try to extract from the element's content
                            rating_text = await rating_element.text_content()
                            if rating_text:
                                # Count stars or try to extract the number
                                if "★" in rating_text:
                                    review_data['review_rating'] = str(rating_text.count("★"))
                                    break
                                else:
                                    rating_match = re.search(r'(\d+(?:\.\d+)?)', rating_text)
                                    if rating_match:
                                        review_data['review_rating'] = rating_match.group(1)
                                        break
                    except:
                        continue
                
                if 'review_rating' not in review_data:
                    review_data['review_rating'] = "0"
                
                # Check for "More" button and click it before extracting review text
                try:
                    more_button_selectors = [
                        'button.w8nwRe',
                        'button:has-text("More")',
                        'button:has-text("mehr")',
                        'button:has-text("Mehr")',
                        'button.review-more-link',
                        'span.review-more-link'
                    ]
                    
                    for selector in more_button_selectors:
                        more_button = await item.query_selector(selector)
                        if more_button:
                            # Check if the button is visible
                            is_visible = await more_button.is_visible()
                            if is_visible:
                                print(f"Clicking 'More' button for review {i+1}")
                                await more_button.click()
                                await asyncio.sleep(0.5)  # Give time for expansion
                                break
                except Exception as e:
                    print(f"Error clicking More button: {e}")
                
                # Extract review text (after clicking More if applicable)
                text_selectors = [
                    'span.wiI7pd',
                    'span.review-full-text',
                    'span[data-expandable-section]',
                    'div.MyEned',
                    'span.review-snippet'
                ]
                
                for selector in text_selectors:
                    try:
                        text_element = await item.query_selector(selector)
                        if text_element:
                            text = await text_element.text_content()
                            if text and text.strip():
                                review_data['review_text'] = text.strip()
                                break
                    except:
                        continue
                
                # Try a more general approach if specific selectors failed
                if 'review_text' not in review_data:
                    try:
                        # Try to get all text content from the review item and clean it
                        all_text = await item.text_content()
                        if all_text:
                            # Remove author and time info if possible
                            author = review_data.get('review_author', '')
                            if author and author in all_text:
                                all_text = all_text.replace(author, '')
                            
                            # Clean and extract the longest paragraph as the review
                            text_parts = [part.strip() for part in all_text.split('\n') if part.strip()]
                            if text_parts:
                                # Get the longest part as the likely review text
                                review_text = max(text_parts, key=len)
                                if len(review_text) > 10:  # Minimum length check
                                    review_data['review_text'] = review_text
                    except:
                        pass
                
                if 'review_text' not in review_data:
                    review_data['review_text'] = ""
                
                # Extract review time
                time_selectors = [
                    'span.rsqaWe',
                    'span.xRkPPd',
                    'span.dehysf',
                    'div.PU6pld'  # This contains both rating stars and time
                ]
                
                for selector in time_selectors:
                    try:
                        time_element = await item.query_selector(selector)
                        if time_element:
                            time_text = await time_element.text_content()
                            if time_text and time_text.strip():
                                review_data['review_time'] = time_text.strip()
                                break
                    except:
                        continue
                
                if 'review_time' not in review_data:
                    review_data['review_time'] = ""
                
                # Detect review language
                if review_data.get('review_text'):
                    try:
                        lang = langdetect.detect(review_data['review_text'])
                        review_data['review_language'] = lang
                    except:
                        review_data['review_language'] = "unknown"
                else:
                    review_data['review_language'] = "unknown"
                
                # Add the review to our collection
                reviews.append(review_data)
                print(f"Extracted review {i+1}: {review_data['review_author']} - {review_data['review_rating']} stars ({review_data['review_language']})")
                
            except Exception as e:
                print(f"Error extracting review {i+1}: {e}")
        
    except Exception as e:
        print(f"Error extracting reviews: {e}")
        await page.screenshot(path="review_extraction_error.png")
    
    print(f"Successfully extracted {len(reviews)} reviews")
    return reviews


# Add a special method to manually handle cookies and other initial setup
async def initial_setup(page: Page) -> None:
    """Perform initial setup tasks before starting the actual scraping."""
    print("Performing initial browser setup...")
    
    # First navigate to Google
    await page.goto("https://www.google.com")
    
    # Try to handle cookies on the Google homepage first
    await accept_cookies(page)
    
    # Wait a moment before continuing
    await asyncio.sleep(2)
    
    print("Initial setup completed")


# Update the scrape_physiotherapists function to use initial_setup
async def scrape_physiotherapists() -> None:
    """Main function to scrape physiotherapist reviews."""
    async with async_playwright() as p:
        # Launch browser with slower execution and viewport settings
        browser = await p.chromium.launch(
            headless=USE_HEADLESS,  # Set to False for debugging, True for production
            slow_mo=200  # Add slight delay between actions to avoid race conditions
        )
        
        context = await browser.new_context(
            viewport={'width': 1366, 'height': 768},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36'
        )
        
        # Create timestamp for unique filenames
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Create results directory
        results_dir = f"results/scrape_{timestamp}"
        os.makedirs(results_dir, exist_ok=True)
        
        # Field names for CSV output
        fieldnames = [
            'place_id', 'company_name', 'address', 'overall_rating', 'total_ratings',
            'coordinates', 'phone', 'website', 'doctolib_url', 'review_author', 'review_rating', 
            'review_text', 'review_time', 'review_language'
        ]
        
        # Create setup page for accepting cookies once
        page = await context.new_page()
        
        try:
            # Perform initial setup - navigate to Google and accept cookies
            print("Performing initial setup (accept cookies)...")
            await initial_setup(page)
            
            # Load search queries from file
            search_queries = []
            try:
                with open('input.txt', 'r', encoding='utf-8') as f:
                    search_queries = [line.strip() for line in f if line.strip()]
            except Exception as e:
                print(f"Error reading input.txt: {e}")
                print("Using default search query")
                search_queries = SEARCH_QUERIES
            
            print(f"Loaded {len(search_queries)} search queries from input.txt")
            
            # Process each search query
            for query_index, search_query in enumerate(search_queries):
                print(f"\n{'='*80}")
                print(f"Processing search query {query_index+1}/{len(search_queries)}: {search_query}")
                print(f"{'='*80}")
                
                # CSV file for this search query
                csv_filename = f"{results_dir}/physio_{search_query.replace(' ', '_')}_{timestamp}.csv"
                
                # Create CSV file and writer
                with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()
                    
                    # Implement retry logic for searching
                    search_success = False
                    for retry in range(MAX_RETRIES):
                        try:
                            print(f"Searching for: {search_query} (attempt {retry+1}/{MAX_RETRIES})")
                            await search_google_maps(page, search_query)
                            print("Search completed successfully")
                            search_success = True
                            
                            # Take a screenshot of search results
                            await page.screenshot(path=f"{results_dir}/search_results_{query_index+1}.png")
                            break  # Exit retry loop if successful
                        except Exception as e:
                            print(f"Error during search attempt {retry+1}: {e}")
                            if retry < MAX_RETRIES - 1:
                                wait_time = (retry + 1) * 3  # Increasing backoff
                                print(f"Waiting {wait_time} seconds before retrying...")
                                await asyncio.sleep(wait_time)
                            else:
                                print("All search attempts failed")
                                try:
                                    await page.screenshot(path=f"{results_dir}/search_error_{query_index+1}.png")
                                except:
                                    print("Failed to take screenshot - page may be closed")
                    
                    if not search_success:
                        print("Could not complete search after multiple attempts, skipping query")
                        continue
                    
                    # Find the search results (business listings)
                    business_selectors = [
                        'div.Nv2PK',
                        'a.hfpxzc',
                        'div[role="article"]'
                    ]
                    
                    business_elements = []
                    for selector in business_selectors:
                        try:
                            elements = await page.query_selector_all(selector)
                            if elements and len(elements) > 0:
                                print(f"Found {len(elements)} business elements with selector: {selector}")
                                
                                # Filter out ad placements
                                filtered_elements = []
                                for element in elements:
                                    # Check if this is an ad (has "Anzeige" or "Ad" text)
                                    is_ad = False
                                    try:
                                        # Look for ad indicators in parent elements
                                        html = await page.evaluate("(element) => element.outerHTML", element)
                                        if any(ad_term in html for ad_term in ["Anzeige", "jHLihd", "Advertisement", "Gesponsert", "Ad"]):
                                            is_ad = True
                                            print("Skipping ad placement")
                                    except:
                                        pass
                                    
                                    if not is_ad:
                                        filtered_elements.append(element)
                                
                                print(f"After filtering ads: {len(filtered_elements)} business elements")
                                business_elements = filtered_elements[:MAX_PLACES]
                                break
                        except Exception as e:
                            print(f"Error finding businesses with selector {selector}: {e}")
                    
                    if not business_elements:
                        print("No business listings found, skipping query")
                        await page.screenshot(path=f"{results_dir}/no_businesses_found_{query_index+1}.png")
                        continue
                    
                    print(f"Processing {len(business_elements)} businesses")
                    
                    # Process each business in the search results
                    for i, business in enumerate(business_elements):
                        try:
                            print(f"\nProcessing business {i+1}/{len(business_elements)}")
                            
                            # Take a screenshot before clicking
                            await page.screenshot(path=f"{results_dir}/before_click_business_{i+1}.png")
                            
                            # Click on the business listing
                            await business.click()
                            print("Clicked on business listing")
                            await asyncio.sleep(3)  # Wait for details to load
                            
                            # Extract place info
                            place_info = {
                                'place_id': f"business_{i+1}_{timestamp}",  # Generate an ID if we can't get a real one
                                'company_name': 'Not found',
                                'address': 'Not found',
                                'overall_rating': '0',
                                'total_ratings': '0',
                                'coordinates': '',  # New field for coordinates
                                'phone': '',        # New field for phone
                                'website': '',      # New field for website
                                'doctolib_url': ''  # New field for doctolib URL
                            }
                            
                            # Get coordinates and actual place ID
                            try:
                                # Extract coordinates from URL
                                current_url = page.url
                                coords_match = re.search(r'@([-\d.]+),([-\d.]+)', current_url)
                                if coords_match:
                                    lat = coords_match.group(1)
                                    lng = coords_match.group(2)
                                    place_info['coordinates'] = f"{lat},{lng}"
                                    print(f"Extracted coordinates: {place_info['coordinates']}")
                                
                                # Try to get the real place ID (ChIJ format) using the page content
                                try:
                                    # Method 1: Check URL for place ID format
                                    place_id_match = re.search(r'place/([^/]+)/([A-Za-z0-9-_]{10,})', current_url)
                                    if place_id_match and not place_id_match.group(2).startswith('@'):
                                        potential_id = place_id_match.group(2)
                                        if re.match(r'^ChIJ|^Eh[A-Za-z0-9-_]{10,}', potential_id):
                                            place_info['place_id'] = potential_id
                                            print(f"Found actual place ID from URL: {potential_id}")
                                    
                                    # Method 2: Look for place ID in page source
                                    if 'place_id' not in place_info or place_info['place_id'].startswith('business_'):
                                        page_content = await page.content()
                                        # Look for patterns like "ChIJ..." or place_id="ChIJ..."
                                        place_id_patterns = [
                                            r'place_id["\s:=]+(["\'])?(ChIJ[A-Za-z0-9-_]{10,})',
                                            r'\"(ChIJ[A-Za-z0-9-_]{10,})\"',
                                            r'\\\"(ChIJ[A-Za-z0-9-_]{10,})\\\"',
                                            r'\"placeId\"\s*:\s*\"(ChIJ[A-Za-z0-9-_]{10,})\"'
                                        ]
                                        
                                        for pattern in place_id_patterns:
                                            matches = re.findall(pattern, page_content)
                                            if matches:
                                                # Handle different match group formats
                                                if isinstance(matches[0], tuple) and len(matches[0]) > 1:
                                                    potential_id = matches[0][1]
                                                else:
                                                    potential_id = matches[0]
                                                    
                                                # Clean up any quotes
                                                potential_id = potential_id.strip('"\'')
                                                
                                                if re.match(r'^ChIJ[A-Za-z0-9-_]{10,}', potential_id):
                                                    place_info['place_id'] = potential_id
                                                    print(f"Found actual place ID from page content: {potential_id}")
                                                    break
                                    
                                    # Method 3: Execute JavaScript to try to get the place ID
                                    if 'place_id' not in place_info or place_info['place_id'].startswith('business_'):
                                        try:
                                            js_result = await page.evaluate('''() => {
                                                // Try to find place ID in any data attributes
                                                const elements = document.querySelectorAll('[data-place-id], [data-pid]');
                                                for (const el of elements) {
                                                    const pid = el.getAttribute('data-place-id') || el.getAttribute('data-pid');
                                                    if (pid && pid.startsWith('ChIJ')) return pid;
                                                }
                                                
                                                // Try to find in any meta tags
                                                const metas = document.querySelectorAll('meta');
                                                for (const meta of metas) {
                                                    const content = meta.getAttribute('content');
                                                    if (content && content.includes('ChIJ')) {
                                                        const match = content.match(/ChIJ[A-Za-z0-9-_]{10,}/);
                                                        if (match) return match[0];
                                                    }
                                                }
                                                
                                                // Try to find in any global variables
                                                for (const key in window) {
                                                    try {
                                                        const value = window[key];
                                                        if (value && typeof value === 'object') {
                                                            const strValue = JSON.stringify(value);
                                                            if (strValue.includes('ChIJ')) {
                                                                const match = strValue.match(/"ChIJ[A-Za-z0-9-_]{10,}"/);
                                                                if (match) return match[0].replace(/"/g, '');
                                                            }
                                                        }
                                                    } catch(e) {
                                                        // Ignore errors
                                                    }
                                                }
                                                
                                                return null;
                                            }''')
                                            
                                            if js_result and js_result.startswith('ChIJ'):
                                                place_info['place_id'] = js_result
                                                print(f"Found actual place ID from JavaScript: {js_result}")
                                        except Exception as js_error:
                                            print(f"Error running JavaScript to find place ID: {js_error}")
                                
                                except Exception as id_error:
                                    print(f"Error extracting place ID: {id_error}")
                            except Exception as e:
                                print(f"Error extracting URL data: {e}")
                            
                            # Extract business name
                            try:
                                # Try to get business name from the title
                                page_title = await page.title()
                                if page_title and " - Google Maps" in page_title:
                                    business_name = page_title.split(" - Google Maps")[0].strip()
                                    if business_name and len(business_name) > 3:
                                        place_info['company_name'] = business_name
                                        print(f"Business name: {business_name}")
                                
                                # If title approach failed, try DOM elements
                                if place_info['company_name'] == 'Not found':
                                    name_selectors = [
                                        'h1.DUwDvf', 
                                        'h1[data-test="hero-title"]',
                                        'div.fontHeadlineLarge',
                                        'h1.fontHeadlineLarge'
                                    ]
                                    
                                    for selector in name_selectors:
                                        name_element = await page.query_selector(selector)
                                        if name_element:
                                            name = await name_element.text_content()
                                            if name and len(name.strip()) > 0:
                                                place_info['company_name'] = name.strip()
                                                print(f"Business name: {place_info['company_name']}")
                                                break
                            except Exception as e:
                                print(f"Error extracting business name: {e}")
                            
                            # Extract address
                            try:
                                address_selectors = [
                                    'button[data-item-id="address"]',
                                    'button[aria-label*="Adresse"]',
                                    'button[aria-label*="address"]'
                                ]
                                
                                for selector in address_selectors:
                                    address_element = await page.query_selector(selector)
                                    if address_element:
                                        address = await address_element.text_content()
                                        if address and len(address.strip()) > 0:
                                            # Clean the address - remove any leading special characters like '?' or other symbols
                                            cleaned_address = re.sub(r'^[^\w]+', '', address.strip())
                                            place_info['address'] = cleaned_address.strip()
                                            print(f"Address: {place_info['address']}")
                                            break
                            except Exception as e:
                                print(f"Error extracting address: {e}")
                            
                            # Extract rating
                            try:
                                rating_selectors = [
                                    'div.F7nice',
                                    'span.F7nice',
                                    'span[aria-hidden="true"][role="img"]'
                                ]
                                
                                for selector in rating_selectors:
                                    rating_element = await page.query_selector(selector)
                                    if rating_element:
                                        rating_text = await rating_element.text_content()
                                        if rating_text:
                                            rating_match = re.search(r'([0-9]+[.,][0-9]+|[0-9]+)', rating_text)
                                            if rating_match:
                                                place_info['overall_rating'] = rating_match.group(1).replace(',', '.')
                                                
                                                total_ratings_match = re.search(r'\(([0-9.,]+)\)', rating_text)
                                                if total_ratings_match:
                                                    place_info['total_ratings'] = total_ratings_match.group(1).replace('.', '').replace(',', '')
                                                break
                            except Exception as e:
                                print(f"Error extracting rating: {e}")
                            
                            # Extract phone number
                            try:
                                phone_selectors = [
                                    'button[data-item-id^="phone:tel:"]',
                                    'button[aria-label*="Telefon"]',
                                    'button[aria-label*="phone"]',
                                    'a[href^="tel:"]',
                                    'div.UsdlK'  # This is the class from the example HTML
                                ]
                                
                                for selector in phone_selectors:
                                    phone_elements = await page.query_selector_all(selector)
                                    for phone_element in phone_elements:
                                        try:
                                            # Try to get the phone from text content
                                            phone_text = await phone_element.text_content()
                                            if phone_text and re.search(r'\d', phone_text):  # Contains at least one digit
                                                # Clean phone number - keep only digits and common phone characters
                                                cleaned_phone = re.sub(r'[^\d\s+\-()]', '', phone_text.strip())
                                                if cleaned_phone:
                                                    place_info['phone'] = cleaned_phone
                                                    print(f"Phone: {place_info['phone']}")
                                                    break
                                            
                                            # Try to get from href attribute for a[href^="tel:"] elements
                                            href = await phone_element.get_attribute('href')
                                            if href and href.startswith('tel:'):
                                                phone = href[4:]  # Remove 'tel:' prefix
                                                if phone:
                                                    place_info['phone'] = phone
                                                    print(f"Phone from href: {place_info['phone']}")
                                                    break
                                        except:
                                            continue
                                    
                                    if place_info['phone']:
                                        break
                                
                                # If still not found, try to search in the page content for phone numbers
                                if not place_info['phone']:
                                    # Try to extract from page content using JavaScript
                                    try:
                                        phone_js_result = await page.evaluate('''() => {
                                            // Look for elements that might contain phone numbers
                                            const phoneRegex = /\\+?[\\d\\s\\(\\)\\-]{7,20}/g;
                                            const textNodes = document.createTreeWalker(
                                                document.body, 
                                                NodeFilter.SHOW_TEXT, 
                                                null, 
                                                false
                                            );
                                            
                                            let node;
                                            let phones = [];
                                            
                                            while(node = textNodes.nextNode()) {
                                                const matches = node.textContent.match(phoneRegex);
                                                if (matches) {
                                                    phones = phones.concat(matches);
                                                }
                                            }
                                            
                                            return phones.length > 0 ? phones[0].trim() : null;
                                        }''')
                                        
                                        if phone_js_result:
                                            place_info['phone'] = phone_js_result
                                            print(f"Phone from JavaScript: {place_info['phone']}")
                                    except Exception as js_error:
                                        print(f"Error extracting phone with JavaScript: {js_error}")
                            except Exception as e:
                                print(f"Error extracting phone: {e}")
                            
                            # Extract website URL
                            try:
                                website_selectors = [
                                    'a[data-item-id="authority"]',
                                    'a[aria-label*="Website"]',
                                    'a.CsEnBe[href]:not([href*="doctolib"])',
                                    'div.rogA2c.ITvuef',  # The container class from the example HTML
                                    'a[href*="www"]'
                                ]
                                
                                for selector in website_selectors:
                                    website_elements = await page.query_selector_all(selector)
                                    for website_element in website_elements:
                                        try:
                                            # Try to get the href attribute
                                            href = await website_element.get_attribute('href')
                                            if href and not href.startswith('tel:'):
                                                # Check if it's a valid website URL
                                                if href.startswith('http') or href.startswith('www'):
                                                    place_info['website'] = href
                                                    print(f"Website: {place_info['website']}")
                                                    break
                                        except:
                                            # If getting href fails, try to get the text content
                                            try:
                                                website_text = await website_element.text_content()
                                                if website_text and ('.' in website_text) and not re.search(r'\s', website_text):
                                                    # This looks like a domain name
                                                    place_info['website'] = f"http://{website_text}"
                                                    print(f"Website from text: {place_info['website']}")
                                                    break
                                            except:
                                                continue
                                    
                                    if place_info['website']:
                                        break
                                
                                # If still not found, try to extract using JavaScript
                                if not place_info['website']:
                                    try:
                                        website_js_result = await page.evaluate('''() => {
                                            // Look for elements that might be website URLs
                                            const links = Array.from(document.querySelectorAll('a[href]'));
                                            for (const link of links) {
                                                const href = link.getAttribute('href');
                                                if (href && 
                                                    (href.startsWith('http') || href.startsWith('www')) &&
                                                    !href.includes('google.com') &&
                                                    !href.includes('tel:') &&
                                                    !href.includes('mailto:') &&
                                                    !href.includes('doctolib')
                                                ) {
                                                    return href;
                                                }
                                            }
                                            return null;
                                        }''')
                                        
                                        if website_js_result:
                                            place_info['website'] = website_js_result
                                            print(f"Website from JavaScript: {place_info['website']}")
                                    except Exception as js_error:
                                        print(f"Error extracting website with JavaScript: {js_error}")
                            except Exception as e:
                                print(f"Error extracting website: {e}")
                            
                            # Extract Doctolib URL
                            try:
                                doctolib_selectors = [
                                    'a[href*="doctolib"]',
                                    'a.CsEnBe[href*="doctolib"]'  # From the example HTML
                                ]
                                
                                for selector in doctolib_selectors:
                                    doctolib_elements = await page.query_selector_all(selector)
                                    for doctolib_element in doctolib_elements:
                                        try:
                                            href = await doctolib_element.get_attribute('href')
                                            if href and 'doctolib' in href:
                                                place_info['doctolib_url'] = href
                                                print(f"Doctolib URL: {place_info['doctolib_url']}")
                                                break
                                        except:
                                            continue
                                    
                                    if place_info['doctolib_url']:
                                        break
                            except Exception as e:
                                print(f"Error extracting Doctolib URL: {e}")
                            
                            # Take a screenshot after extracting details
                            await page.screenshot(path=f"{results_dir}/business_details_{i+1}.png")
                            
                            # Now navigate to the reviews tab - directly using the structure from the user's example
                            try:
                                print("Trying to navigate to reviews tab...")
                                
                                # Try using the exact tab selector from the user's example
                                review_tab = await page.query_selector('button[role="tab"][aria-label*="Rezension"], button[role="tab"][data-tab-index="1"]')
                                if review_tab:
                                    print("Found reviews tab, clicking it...")
                                    await review_tab.click()
                                    await asyncio.sleep(2)  # Wait for reviews to load
                                else:
                                    # Try more general selectors
                                    review_tab_selectors = [
                                        'button[role="tab"]:has-text("Rezension")', 
                                        'button[role="tab"]:has-text("Review")',
                                        'button[data-tab-index="1"]'
                                    ]
                                    
                                    for selector in review_tab_selectors:
                                        tab = await page.query_selector(selector)
                                        if tab:
                                            print(f"Found reviews tab with selector: {selector}")
                                            await tab.click()
                                            await asyncio.sleep(2)  # Wait for reviews to load
                                            break
                                
                                # Take screenshot after clicking reviews tab
                                await page.screenshot(path=f"{results_dir}/reviews_tab_{i+1}.png")
                                
                                # Extract reviews using our updated function
                                reviews = await extract_reviews(page, MAX_REVIEWS_PER_PLACE)
                                
                                # Write reviews to CSV
                                if reviews:
                                    for review in reviews:
                                        # Combine place info with review info
                                        row = {**place_info, **review}
                                        writer.writerow(row)
                                        csvfile.flush()  # Ensure data is written immediately
                                else:
                                    # If no reviews found, still write place info
                                    empty_review = {
                                        'review_author': '',
                                        'review_rating': '',
                                        'review_text': '',
                                        'review_time': '',
                                        'review_language': ''
                                    }
                                    row = {**place_info, **empty_review}
                                    writer.writerow(row)
                                    csvfile.flush()
                            except Exception as e:
                                print(f"Error processing reviews: {e}")
                                
                                # Still save the business info without reviews
                                empty_review = {
                                    'review_author': '',
                                    'review_rating': '',
                                    'review_text': '',
                                    'review_time': '',
                                    'review_language': ''
                                }
                                row = {**place_info, **empty_review}
                                writer.writerow(row)
                                csvfile.flush()
                            
                            # Go back to search results
                            print("Navigating back to search results...")
                            await page.go_back()
                            await asyncio.sleep(2)  # Wait for search results to load again
                            
                        except Exception as e:
                            print(f"Error processing business {i+1}: {e}")
                            
                            # Try to go back to search results
                            try:
                                await page.go_back()
                                await asyncio.sleep(2)
                            except:
                                print("Failed to go back, trying to search again")
                                # If going back fails, try searching again
                                await search_google_maps(page, search_query)
                    
                print(f"\nCompleted search query: {search_query}")
                print(f"Results saved to: {csv_filename}")
                
                # Add pause between search queries
                if query_index < len(search_queries) - 1:
                    pause_time = 5  # 5 seconds pause between search queries
                    print(f"Pausing for {pause_time} seconds before next search query...")
                    await asyncio.sleep(pause_time)
            
            print(f"\nAll search queries processed. Results saved in: {results_dir}")
        
        except Exception as e:
            print(f"An error occurred in the main process: {e}")
            traceback.print_exc()
        
        finally:
            # Always close the browser
            await browser.close()
            print("Browser closed.")


if __name__ == "__main__":
    # Check if input file exists and load search queries
    input_file = 'input.txt'
    if os.path.exists(input_file):
        try:
            with open(input_file, 'r', encoding='utf-8') as f:
                queries = [line.strip() for line in f if line.strip()]
                if queries:
                    print(f"Loaded {len(queries)} search queries from {input_file}")
                    SEARCH_QUERIES = queries
        except Exception as e:
            print(f"Error reading input file: {e}")
    
    try:
        asyncio.run(scrape_physiotherapists())
    except KeyboardInterrupt:
        print("\nScript was interrupted by user. Exiting gracefully...")
    except Exception as e:
        print(f"\nUnhandled exception: {e}") 