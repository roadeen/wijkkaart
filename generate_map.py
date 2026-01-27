import pandas as pd
import folium
from folium.plugins import MarkerCluster, LocateControl
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from github import Github, Auth
import os
import time
import socket
import json

# ----------------------------
# 1. Instellingen
# ----------------------------
sheet_name = "Adressen_Checklist"

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
if not GITHUB_TOKEN:
    raise ValueError("GITHUB_TOKEN environment variable not set.")

REPO_NAME = "roadeen/wijkkaart"
FILE_PATH_IN_REPO = "index.html"
LOCAL_OUTPUT = "index.html"
OPMERKING_COLOR = '#9b59b6'  # Purple

# ----------------------------
# 2. Google Setup
# ----------------------------
def get_credentials():
    creds_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    if creds_json:
        creds_dict = json.loads(creds_json)
        return ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        return ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)

def generate_interactive_map():
    start_time = time.time()
    
    print("☁️ Data ophalen uit Google Sheets...")
    try:
        creds = get_credentials()
        client = gspread.authorize(creds)
        sheet = client.open(sheet_name).sheet1
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
    except Exception as e:
        print(f"❌ Google Sheets fout: {e}")
        return

    print(f"\n📊 Totaal adressen: {len(df)}")
    
    # --- B. Kaart initialiseren ---
    m = folium.Map(
        location=[df['lat'].mean(), df['lon'].mean()], 
        zoom_start=16, 
        tiles='cartodbpositron',
        control_scale=True
    )

    LocateControl(auto_start=False, flyTo=True).add_to(m)

    icon_create_function = f"""
    function(cluster) {{
        var childMarkers = cluster.getAllChildMarkers();
        var totalAddresses = 0;
        var doneAddresses = 0;
        var hasOpmerking = false;
        
        // Calculate totals from all addresses in all group markers
        childMarkers.forEach(function(marker) {{
            totalAddresses += marker.options.totalAddresses || 1;
            doneAddresses += marker.options.doneAddresses || 0;
            if (marker.options.hasOpmerking) {{
                hasOpmerking = true;
            }}
        }});
        
        // Use child markers count for cluster size (not address count)
        var childCount = cluster.getChildCount();
        var percentage = totalAddresses > 0 ? (doneAddresses / totalAddresses) * 100 : 0;
        var color;
        
        if (percentage === 100) {{ color = '#28a745'; }}
        else if (percentage >= 75) {{ color = '#7cb342'; }}
        else if (percentage >= 50) {{ color = '#ffc107'; }}
        else if (percentage >= 25) {{ color = '#fd7e14'; }}
        else {{ color = '#dc3545'; }}
        
        var borderColor = hasOpmerking ? '{OPMERKING_COLOR}' : 'white';
        var borderWidth = hasOpmerking ? '4px' : '3px';
        
        // Show number of markers in cluster, not total addresses
        return L.divIcon({{
            html: '<div style="background-color:' + color + '; width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; border: ' + borderWidth + ' solid ' + borderColor + '; box-shadow: 0 2px 5px rgba(0,0,0,0.3);"><span style="color: white; font-weight: bold; font-size: 14px;">' + childCount + '</span></div>',
            className: 'marker-cluster-custom',
            iconSize: L.point(40, 40)
        }});
    }}
    """

    marker_cluster = MarkerCluster(
        name='Adressen',
        overlay=True,
        control=True,
        icon_create_function=icon_create_function,
        options={
            'maxClusterRadius': 30,
            'disableClusteringAtZoom': 19,
            'spiderfyOnMaxZoom': True,
            'showCoverageOnHover': False,
            'spiderfyDistanceMultiplier': 1.5,
            'singleMarkerMode': False,
            'zoomToBoundsOnClick': True
        }
    )

    skipped_addresses = []
    added_count = 0
    opmerking_count = 0
    
    # Dictionary to group addresses by location
    address_groups = {}

    # First pass: Group addresses by coordinates
    for idx, row in df.iterrows():
        try:
            lat = float(row['lat'])
            lon = float(row['lon'])
            
            if lat == 0 or lon == 0:
                skipped_addresses.append(f"{row['Adres']} (lat=0, lon=0)")
                continue
            
            # Check range
            if not (50.5 <= lat <= 53.7 and 3.0 <= lon <= 7.5):
                skipped_addresses.append(f"{row['Adres']} (buiten NL)")
                continue
            
            # Group by coordinates
            loc_key = f"{lat:.6f},{lon:.6f}"
            
            if loc_key not in address_groups:
                address_groups[loc_key] = {
                    'lat': lat,
                    'lon': lon,
                    'addresses': []
                }
            
            address_groups[loc_key]['addresses'].append(row)
            
        except Exception as e:
            skipped_addresses.append(f"{row.get('Adres', 'Onbekend')} (fout: {e})")

    # Second pass: Create one marker per group
    for loc_key, group_data in address_groups.items():
        lat = group_data['lat']
        lon = group_data['lon']
        addresses = group_data['addresses']
        
        # Count opmerkingen for this group
        group_opmerking_count = 0
        opmerkingen_list = []
        done_addresses = 0
        
        # Start building popup content
        popup_content = f"""
        <div style='min-width: 250px; max-width: 400px; font-family: Arial, sans-serif;'>
            <div style='background-color: #f8f9fa; padding: 12px; border-radius: 5px; margin-bottom: 12px; border-left: 4px solid #007bff;'>
                <b style='font-size: 15px; color: #333;'>📍 {len(addresses)} adres{'sen' if len(addresses) > 1 else ''} op deze locatie</b>
            </div>
            <div style='max-height: 400px; overflow-y: auto; padding-right: 5px;'>
        """
        
        all_done = True
        has_opmerking = False
        
        for idx, row in enumerate(addresses):
            is_done = str(row.get('Afgevinkt', '')).strip().lower() == 'ja'
            if is_done:
                done_addresses += 1
            else:
                all_done = False
            
            adres = row.get('Adres', 'Onbekend adres')
            
            # Check voor opmerkingen
            row_opmerking = ""
            if 'Opmerkingen' in row and row['Opmerkingen']:
                opmerking_text = str(row['Opmerkingen']).strip()
                if opmerking_text and opmerking_text.lower() != 'nan':
                    has_opmerking = True
                    group_opmerking_count += 1
                    opmerking_text = opmerking_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    
                    # Add to list for summary
                    opmerkingen_list.append({
                        'adres': adres,
                        'text': opmerking_text
                    })
                    
                    row_opmerking = f"""
                    <div style='word-wrap: break-word; overflow-wrap: break-word; white-space: normal; 
                                margin: 5px 0; padding: 8px; background: #f8f0ff; border-radius: 4px;
                                border-left: 3px solid {OPMERKING_COLOR}; font-size: 12px;'>
                        <b>💬 Opmerking:</b> {opmerking_text}
                    </div>
                    """
            
            # Determine color for status indicator
            status_color = '#28a745' if is_done else '#dc3545'
            status_icon = '✅' if is_done else '❌'
            
            popup_content += f"""
            <div style='margin-bottom: 12px; padding-bottom: 12px; 
                        border-bottom: {'1px solid #eee' if idx < len(addresses) - 1 else 'none'};'>
                <div style='display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 5px;'>
                    <b style='font-size: 13px; line-height: 1.4; flex: 1; margin-right: 10px;'>{idx+1}. {adres}</b>
                    <span style='font-size: 11px; color: {status_color}; font-weight: bold; white-space: nowrap;'>
                        {status_icon} {'Afgevinkt' if is_done else 'Niet afgevinkt'}
                    </span>
                </div>
                {row_opmerking}
            </div>
            """
        
        popup_content += "</div>"  # Close the scrollable div
        
        # Add opmerkingen summary if any
        if has_opmerking:
            opmerking_count += group_opmerking_count
            
            popup_content += f"""
            <div style='background-color: #f8f0ff; padding: 12px; border-radius: 5px; margin-top: 15px; 
                        border-left: 4px solid {OPMERKING_COLOR};'>
                <b style='color: {OPMERKING_COLOR}; font-size: 14px;'>💬 Opmerkingen op deze locatie ({group_opmerking_count}):</b>
            """
            
            for opm in opmerkingen_list:
                popup_content += f"""
                <div style='margin: 8px 0; padding: 8px; background: white; border-radius: 4px;'>
                    <b style='font-size: 12px;'>{opm['adres']}:</b>
                    <div style='word-wrap: break-word; overflow-wrap: break-word; white-space: normal; 
                                font-size: 12px; color: #555; margin-top: 4px;'>
                        {opm['text']}
                    </div>
                </div>
                """
            
            popup_content += "</div>"
        
        popup_content += "</div>"
        
        # Calculate percentage done for this group
        total_addresses = len(addresses)
        percentage_done = (done_addresses / total_addresses) * 100 if total_addresses > 0 else 0
        
        # Determine fill color based on percentage done (same logic as clusters)
        if percentage_done == 100:
            fill_color = '#28a745'  # Green
        elif percentage_done >= 75:
            fill_color = '#7cb342'  # Light green
        elif percentage_done >= 50:
            fill_color = '#ffc107'  # Yellow
        elif percentage_done >= 25:
            fill_color = '#fd7e14'  # Orange
        else:
            fill_color = '#dc3545'  # Red
        
        # Determine border color and width
        border_color = OPMERKING_COLOR if has_opmerking else 'white'
        border_width = 3 if has_opmerking else 1.5
        
        # Adjust marker size based on number of addresses (7-12px radius)
        marker_size = 7 + min(len(addresses) - 1, 5)
        
        # Create the marker with mobile-friendly popup settings
        marker = folium.CircleMarker(
            location=[lat, lon],
            radius=marker_size,
            popup=folium.Popup(
                popup_content, 
                max_width=450,
                max_height=500,
                sticky=False,
                close_button=True,
                auto_close=False,
                close_on_escape_key=True,
                keep_in_front=True
            ),
            color=border_color,  # Border color
            weight=border_width,  # Border width
            fill=True,
            fillColor=fill_color,  # Fill color based on percentage done
            fillOpacity=0.85,
            tooltip=f"{len(addresses)} adres{'sen' if len(addresses) > 1 else ''}"
        )
        
        # Store metadata for clustering
        marker.options['done'] = all_done
        marker.options['hasOpmerking'] = has_opmerking
        marker.options['addressCount'] = len(addresses)
        marker.options['totalAddresses'] = len(addresses)
        marker.options['doneAddresses'] = done_addresses
        
        # Add to cluster
        marker.add_to(marker_cluster)
        added_count += len(addresses)

    marker_cluster.add_to(m)

    # Add CSS for better mobile experience
    m.get_root().html.add_child(folium.Element("""
    <style>
        .leaflet-popup-content-wrapper {
            border-radius: 8px;
            -webkit-overflow-scrolling: touch; /* For smooth scrolling on iOS */
        }
        .leaflet-popup-content {
            margin: 0;
            padding: 0;
        }
        .leaflet-popup-tip {
            width: 12px;
            height: 12px;
        }
        .leaflet-container a.leaflet-popup-close-button {
            padding: 10px;
            font-size: 16px;
            color: #666;
        }
        .leaflet-popup {
            pointer-events: auto !important;
        }
        @media (max-width: 768px) {
            .leaflet-popup {
                max-width: 90vw !important;
            }
            .leaflet-popup-content-wrapper {
                max-height: 70vh;
                overflow: hidden;
            }
        }
        
        /* Custom styling for individual markers with opmerkingen */
        .marker-with-opmerking {
            filter: drop-shadow(0 0 2px rgba(155, 89, 182, 0.5));
        }
    </style>
    """))

    # Summary statistics
    print(f"📍 Groepen gemaakt: {len(address_groups)}")
    print(f"📝 Opmerkingen geteld: {opmerking_count}")
    
    if skipped_addresses:
        print(f"\n⚠️  Overgeslagen adressen ({len(skipped_addresses)}):")
        for addr in skipped_addresses[:10]:  # Show first 10
            print(f"   - {addr}")
        if len(skipped_addresses) > 10:
            print(f"   ... en {len(skipped_addresses) - 10} meer")

    # Save and Upload
    m.save(LOCAL_OUTPUT)
    
    # Also add mobile meta tags to the HTML
    with open(LOCAL_OUTPUT, "r", encoding='utf-8') as f:
        content = f.read()
    
    # Add mobile-friendly meta tags
    meta_tags = '<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">'
    content = content.replace('<head>', '<head>' + meta_tags)
    
    with open(LOCAL_OUTPUT, "w", encoding='utf-8') as f:
        f.write(content)
    
    with open(LOCAL_OUTPUT, "r", encoding='utf-8') as f:
        content = f.read()

    try:
        auth = Auth.Token(GITHUB_TOKEN)
        g = Github(auth=auth)
        repo = g.get_repo(REPO_NAME)
        contents = repo.get_contents(FILE_PATH_IN_REPO)
        repo.update_file(contents.path, f"Update: {added_count} markers ({len(address_groups)} groepen)", content, contents.sha)
        print(f"✅ Succes! {added_count} adressen in {len(address_groups)} markers op de kaart.")
    except Exception as e:
        print(f"❌ GitHub fout: {e}")

if __name__ == '__main__':
    generate_interactive_map()
