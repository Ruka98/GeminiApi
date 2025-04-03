import googlemaps
import csv
import time
import os
import google.generativeai as genai
import requests
from PIL import Image
from io import BytesIO
import base64
from urllib.parse import urlencode
from shapely.geometry import Point
import folium
from folium.plugins import MarkerCluster
import tempfile
import zipfile
import streamlit as st
from streamlit_folium import folium_static

# API keys - Note: In production, use st.secrets or environment variables
GOOGLE_MAPS_API_KEY = "AIzaSyCVEZmawt1ZHi1f0kdkoRiyN8t7FeUFOhQ"  # Replace with your actual Google Maps API key
GEMINI_API_KEY = "AIzaSyBukl2UGZifHvZd7H9NQPc97L9Hk-QQwJY"

# Initialize clients
gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)
genai.configure(api_key=GEMINI_API_KEY)
model_vision = genai.GenerativeModel('gemini-1.5-flash')

def get_zoomed_map_url(lat, lng, zoom=18, size="600x600"):
    """Get satellite view at zoom level 18 for detailed analysis"""
    base_url = "https://maps.googleapis.com/maps/api/staticmap?"
    params = {
        "center": f"{lat},{lng}",
        "zoom": zoom,
        "size": size,
        "maptype": "satellite",
        "key": GOOGLE_MAPS_API_KEY
    }
    return base_url + urlencode(params)

def verify_wwtp_image(image_base64):
    contents = [
        {"mime_type": "image/jpeg", "data": image_base64},
        """Analyze this high-resolution satellite image (zoom level 18, 600x600 pixels) for wastewater treatment plant (WWTP) features:
        1. Circular/oval structures (clarifiers, digesters, 20-50m diameter)
        2. Rectangular basins (aeration tanks, settling ponds)
        3. Water presence in structures (darker blue/gray)
        4. Pipeline networks
        5. Sludge processing areas
        6. Industrial water treatment layout
        
        Requirements:
        - MUST have visible water in structures
        - Multiple water-containing structures increase confidence
        
        Additional Task:
        - Estimate the WWTP capacity in Million Liters per Day (MLD) based on the visible water area
        - At zoom 18, 1 pixel ≈ 0.6 meters; image covers ~360m x 360m
        - Rough guideline: Small (<10 MLD), Medium (10-50 MLD), Large (>50 MLD) based on water surface area
        
        Respond with:
        [VERDICT] YES/NO/MAYBE
        [WATER_PRESENT] YES/NO
        [REASONING] Detailed analysis
        [CONFIDENCE] Low/Medium/High
        [CAPACITY_ESTIMATE] Estimated capacity in MLD (e.g., '5 MLD', '25 MLD', '60 MLD')"""
    ]
    try:
        response = model_vision.generate_content(contents)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini Vision API error: {e}")
        return "ERROR: Gemini Vision API"

def parse_verification_response(response):
    verdict = "NO"
    water_present = "NO"
    reasoning = ""
    confidence = "Low"
    capacity_estimate = "Unknown"
    
    if "[VERDICT]" in response:
        lines = response.split('\n')
        for line in lines:
            if line.startswith("[VERDICT]"): verdict = line.replace("[VERDICT]", "").strip()
            elif line.startswith("[WATER_PRESENT]"): water_present = line.replace("[WATER_PRESENT]", "").strip()
            elif line.startswith("[REASONING]"): reasoning = line.replace("[REASONING]", "").strip()
            elif line.startswith("[CONFIDENCE]"): confidence = line.replace("[CONFIDENCE]", "").strip()
            elif line.startswith("[CAPACITY_ESTIMATE]"): capacity_estimate = line.replace("[CAPACITY_ESTIMATE]", "").strip()
    
    return verdict, water_present, reasoning, confidence, capacity_estimate

def create_interactive_map(verified_wwtp, center_lat, center_lng):
    """Create an interactive Folium map with Google Maps satellite base layer"""
    m = folium.Map(
        location=[center_lat, center_lng],
        zoom_start=10,
        tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
        attr='Google Satellite'
    )
    
    # Add Google Maps satellite layer as the default
    folium.TileLayer(
        tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
        attr='Google Satellite',
        name='Satellite View',
        overlay=False,
        control=True
    ).add_to(m)
    
    # Add regular Google Maps layer as an option
    folium.TileLayer(
        tiles='https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}',
        attr='Google Maps',
        name='Map View',
        overlay=False,
        control=True
    ).add_to(m)
    
    # Add marker cluster for better visualization of multiple points
    marker_cluster = MarkerCluster().add_to(m)
    
    # Add verified WWTP markers with popups showing details
    for wwtp in verified_wwtp:
        name, lat, lng, address, status, map_url, reasoning, capacity = wwtp
        
        # Create popup content with HTML formatting
        popup_content = f"""
        <div style="width: 250px;">
            <h4 style="margin-bottom: 5px;">{name}</h4>
            <hr style="margin: 5px 0;">
            <p style="margin: 3px 0;"><b>Status:</b> {status}</p>
            <p style="margin: 3px 0;"><b>Capacity:</b> {capacity}</p>
            <p style="margin: 3px 0;"><b>Address:</b> {address}</p>
            <p style="margin: 3px 0;"><b>Confidence:</b> {status.split('(')[-1].replace(')', '')}</p>
            <a href="{map_url}" target="_blank" style="color: blue; text-decoration: underline;">View Satellite Image</a>
            <hr style="margin: 5px 0;">
            <p style="margin: 3px 0; font-size: 12px;"><b>Analysis:</b> {reasoning[:150]}...</p>
        </div>
        """
        
        # Create marker with custom icon and popup
        folium.Marker(
            location=[lat, lng],
            popup=folium.Popup(popup_content, max_width=300),
            tooltip=f"{name} ({capacity})",
            icon=folium.Icon(
                color='blue',
                icon='tint',
                prefix='fa'
            )
        ).add_to(marker_cluster)
    
    # Add layer control to toggle between map and satellite
    folium.LayerControl().add_to(m)
    
    return m

def search_within_radius(lat, lng, radius_km):
    """Search for WWTPs within the specified radius from coordinates"""
    try:
        location = (float(lat), float(lng))
        radius_meters = int(float(radius_km) * 1000)  # Convert km to meters
        
        all_plants = []
        verified_wwtp = []
        search_keywords = ["wastewater treatment plant", "sewage treatment plant", 
                         "water treatment facility", "WWTP", "STP"]
        
        all_places = []
        for keyword in search_keywords:
            st.write(f"Searching for: {keyword} at {lat}, {lng}")
            places_result = gmaps.places_nearby(location=location, radius=radius_meters, keyword=keyword)
            while places_result and places_result.get('results'):
                all_places.extend(places_result['results'])
                next_page_token = places_result.get('next_page_token')
                if not next_page_token:
                    break
                time.sleep(2)
                places_result = gmaps.places_nearby(page_token=next_page_token)

        unique_places = {place['place_id']: place for place in all_places}.values()
        
        for place in unique_places:
            name = place['name']
            place_lat = place['geometry']['location']['lat']
            place_lng = place['geometry']['location']['lng']
            place_id = place['place_id']

            all_plants.append([name, place_lat, place_lng])
            
            place_details = gmaps.place(place_id, fields=['formatted_address'])
            address = place_details['result'].get('formatted_address', '')

            map_url = get_zoomed_map_url(place_lat, place_lng, zoom=18)
            try:
                response = requests.get(map_url)
                response.raise_for_status()
                image = Image.open(BytesIO(response.content)).convert('RGB')
                buffered = BytesIO()
                image.save(buffered, format="JPEG", quality=90)
                img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
            except Exception as e:
                st.warning(f"Image error for {name}: {e}")
                continue

            verification_result = verify_wwtp_image(img_base64)
            verdict, water_present, reasoning, confidence, capacity_estimate = parse_verification_response(verification_result)
            
            st.write(f"\n--- {name} ---")
            st.write(f"Verdict: {verdict}, Water: {water_present}, Confidence: {confidence}, Capacity: {capacity_estimate}")
            
            if (verdict.upper() in ["YES", "MAYBE"] and 
                water_present.upper() == "YES" and 
                confidence.upper() in ["MEDIUM", "HIGH"]):
                status = f"Verified WWTP - Water Present ({confidence})"
                verified_wwtp.append([name, place_lat, place_lng, address, status, map_url, reasoning, capacity_estimate])
                st.success(f"✅ Added as WWTP: {name}")

        return all_plants, verified_wwtp

    except Exception as e:
        st.error(f"Search error: {e}")
        return [], []

def create_zip_file(all_plants, verified_wwtp, map_html, output_dir):
    """Create a zip file with all the output files"""
    zip_path = os.path.join(output_dir, "wwtp_search_results.zip")
    
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        # Add CSV files
        all_plants_path = os.path.join(output_dir, "all_searched_plants.csv")
        verified_wwtp_path = os.path.join(output_dir, "verified_wwtp_locations.csv")
        
        with open(all_plants_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["Name", "Latitude", "Longitude"])
            writer.writerows(all_plants)
        zipf.write(all_plants_path, "all_searched_plants.csv")
        
        with open(verified_wwtp_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["Name", "Latitude", "Longitude", "Address", "Status", "Map URL", "Reasoning", "Estimated Capacity (MLD)"])
            writer.writerows(verified_wwtp)
        zipf.write(verified_wwtp_path, "verified_wwtp_locations.csv")
        
        # Add map HTML file
        map_path = os.path.join(output_dir, "verified_wwtp_map.html")
        with open(map_path, 'w', encoding='utf-8') as f:
            f.write(map_html)
        zipf.write(map_path, "verified_wwtp_map.html")
    
    return zip_path

def main():
    st.set_page_config(page_title="WWTP Search Tool", layout="wide")
    st.title("WWTP Search by Coordinates")
    
    with st.sidebar:
        st.header("Search Parameters")
        lat = st.text_input("Latitude:", help="Enter the latitude coordinate")
        lng = st.text_input("Longitude:", help="Enter the longitude coordinate")
        radius_km = st.text_input("Search Radius (km):", value="300", help="Radius in kilometers to search around the coordinates")
        
        output_dir = st.text_input("Output Directory:", value=os.getcwd(), help="Directory to save results")
        st.button("Browse", help="Select output directory")
        
        if st.button("Run Search"):
            if not lat or not lng:
                st.error("Please enter both latitude and longitude")
                return
            try:
                float(lat)
                float(lng)
            except ValueError:
                st.error("Latitude and Longitude must be numbers")
                return
                
            try:
                radius_km = float(radius_km)
                if radius_km <= 0:
                    raise ValueError
            except ValueError:
                st.error("Radius must be a positive number")
                return
            
            os.makedirs(output_dir, exist_ok=True)
            
            with st.spinner("Searching for WWTP locations..."):
                all_plants, verified_wwtp = search_within_radius(lat, lng, radius_km)
                
                if all_plants or verified_wwtp:
                    st.success(f"Search completed!\nAll plants: {len(all_plants)}\nVerified WWTPs: {len(verified_wwtp)}")
                    
                    # Create interactive map
                    if verified_wwtp:
                        m = create_interactive_map(verified_wwtp, float(lat), float(lng))
                        map_html = m.get_root().render()
                        
                        # Display the map
                        st.subheader("Interactive Map of Verified WWTPs")
                        folium_static(m)
                        
                        # Create zip file with all outputs
                        zip_path = create_zip_file(all_plants, verified_wwtp, map_html, output_dir)
                        
                        # Download button for the zip file
                        with open(zip_path, "rb") as f:
                            st.download_button(
                                label="Download All Results as ZIP",
                                data=f,
                                file_name="wwtp_search_results.zip",
                                mime="application/zip"
                            )
                else:
                    st.warning("No WWTPs found in the specified area")

if __name__ == "__main__":
    main()