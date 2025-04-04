#!/usr/bin/env python
import os
import sys
import folium
from folium.plugins import HeatMap
from bs4 import BeautifulSoup
import re
import json
from datetime import datetime

def extract_data_from_html(html_file):
    """Extract map data from an existing Folium HTML file."""
    print(f"Extracting data from: {html_file}")
    
    try:
        with open(html_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Parse the HTML content
        soup = BeautifulSoup(content, 'html.parser')
        
        # Extract Javascript data from the page
        scripts = soup.find_all('script')
        
        # Data containers
        grid_points = []
        refinement_points = []
        place_points = []
        
        # Pattern for extracting coordinate data
        circle_pattern = re.compile(r'L\.circleMarker\(\s*\[([0-9.-]+),\s*([0-9.-]+)\]')
        marker_pattern = re.compile(r'L\.marker\(\s*\[([0-9.-]+),\s*([0-9.-]+)\]')
        popup_pattern = re.compile(r'\.bindPopup\(["\']([^"\']+)["\']\)')
        
        for script in scripts:
            script_text = script.string
            if not script_text:
                continue
                
            # Extract circle markers (grid and refinement points)
            for line in script_text.splitlines():
                if 'L.circleMarker' in line:
                    match = circle_pattern.search(line)
                    if match:
                        lat, lng = float(match.group(1)), float(match.group(2))
                        
                        # Determine if it's a standard grid or refinement point based on style
                        if 'color: "blue"' in line or 'color: \'blue\'' in line:
                            grid_points.append((lat, lng))
                        elif 'color: "red"' in line or 'color: \'red\'' in line:
                            refinement_points.append((lat, lng))
                
                elif 'L.marker' in line:
                    match = marker_pattern.search(line)
                    if match:
                        lat, lng = float(match.group(1)), float(match.group(2))
                        
                        # Try to get the popup content to extract place_id
                        popup_match = popup_pattern.search(line)
                        if popup_match:
                            popup_content = popup_match.group(1)
                            # Extract place_id if available
                            place_id = popup_content
                            if "Place ID:" in popup_content:
                                place_id = popup_content.replace("Place ID:", "").strip()
                            
                            place_points.append((place_id, lat, lng))
                        else:
                            # If no popup, just use a placeholder ID
                            place_points.append((f"unknown_{lat}_{lng}", lat, lng))
        
        print(f"  - Found {len(grid_points)} grid points")
        print(f"  - Found {len(refinement_points)} refinement points")
        print(f"  - Found {len(place_points)} place markers")
        
        return grid_points, refinement_points, place_points
        
    except Exception as e:
        print(f"Error extracting data from {html_file}: {e}")
        return [], [], []

def create_combined_map(datasets, output_file):
    """Create a combined map with data from multiple HTML files.
    
    Args:
        datasets: List of (name, grid_points, refinement_points, place_points) tuples
        output_file: Path to save the combined HTML map
    """
    # Prepare colors for multiple datasets
    color_sets = [
        {'grid': 'blue', 'refinement': 'red', 'places': 'green'},
        {'grid': 'darkblue', 'refinement': 'darkred', 'places': 'darkgreen'},
        {'grid': 'purple', 'refinement': 'orange', 'places': 'cadetblue'},
        {'grid': 'gray', 'refinement': 'black', 'places': 'darkpurple'},
        {'grid': 'lightblue', 'refinement': 'pink', 'places': 'lightgreen'}
    ]
    
    # Calculate center point from all data
    all_points = []
    for _, grid, refine, places in datasets:
        all_points.extend(grid)
        all_points.extend([(lat, lng) for _, lat, lng in places])
    
    if all_points:
        # Average of all points
        center_lat = sum(lat for lat, _ in all_points) / len(all_points)
        center_lng = sum(lng for _, lng in all_points) / len(all_points)
    else:
        # Default to Berlin center
        center_lat, center_lng = 52.52, 13.41
    
    # Create the map
    m = folium.Map(location=[center_lat, center_lng], zoom_start=10)
    
    # Add dataset layers
    heatmap_data = []
    
    for idx, (name, grid, refine, places) in enumerate(datasets):
        colors = color_sets[idx % len(color_sets)]
        dataset_name = os.path.basename(name).replace('.html', '').replace('map_physiotherapist_berlin_', '')
        
        # Create feature groups for this dataset
        grid_layer = folium.FeatureGroup(name=f"Grid Points ({dataset_name})")
        refine_layer = folium.FeatureGroup(name=f"Refinement Points ({dataset_name})")
        places_layer = folium.FeatureGroup(name=f"Physiotherapists ({dataset_name})")
        
        # Add grid points
        for lat, lng in grid:
            folium.CircleMarker(
                location=[lat, lng],
                radius=4,
                color=colors['grid'],
                fill=True,
                fill_opacity=0.4,
                popup=f"Grid ({dataset_name}): {lat:.6f}, {lng:.6f}"
            ).add_to(grid_layer)
        
        # Add refinement points
        for lat, lng in refine:
            folium.CircleMarker(
                location=[lat, lng],
                radius=3,
                color=colors['refinement'],
                fill=True,
                fill_opacity=0.6,
                popup=f"Refinement ({dataset_name}): {lat:.6f}, {lng:.6f}"
            ).add_to(refine_layer)
        
        # Add place markers
        for place_id, lat, lng in places:
            folium.Marker(
                location=[lat, lng],
                popup=f"Place ID: {place_id}",
                icon=folium.Icon(color=colors['places'], icon='info-sign')
            ).add_to(places_layer)
            heatmap_data.append([lat, lng, 1])
        
        # Add layers to map
        grid_layer.add_to(m)
        refine_layer.add_to(m)
        places_layer.add_to(m)
    
    # Add heatmap layer
    if heatmap_data:
        heatmap_layer = folium.FeatureGroup(name="Density Heatmap (Combined)")
        HeatMap(heatmap_data).add_to(heatmap_layer)
        heatmap_layer.add_to(m)
    
    # Add layer control
    folium.LayerControl().add_to(m)
    
    # Save map
    m.save(output_file)
    print(f"Combined map saved to: {output_file}")

def main():
    if len(sys.argv) < 3:
        print("Usage: python combine_maps.py output_file.html input_map1.html input_map2.html [input_map3.html ...]")
        sys.exit(1)
    
    output_file = sys.argv[1]
    input_files = sys.argv[2:]
    
    print(f"Combining {len(input_files)} maps into: {output_file}")
    
    # Extract data from each input file
    datasets = []
    for input_file in input_files:
        grid, refine, places = extract_data_from_html(input_file)
        if grid or refine or places:
            datasets.append((input_file, grid, refine, places))
    
    if not datasets:
        print("No valid data found in input files.")
        sys.exit(1)
    
    # Create combined map
    create_combined_map(datasets, output_file)

if __name__ == "__main__":
    main() 