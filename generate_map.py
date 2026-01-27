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
        tiles='cartodbpositron'
    )

    LocateControl(auto_start=False, flyTo=True).add_to(m)

    icon_create_function = f"""
    function(cluster) {{
        var markers = cluster.getAllChildMarkers();
        var total = markers.length;
        var done = 0;
        var hasOpmerking = false;
        
        markers.forEach(function(marker) {{
            if (marker.options.hasOpmerking) {{
                hasOpmerking = true;
            }}
            if (marker.options.done) {{
                done++;
            }}
        }});
        
        var percentage = (done / total) * 100;
        var color;
        
        if (percentage === 100) {{ color = '#28a745'; }}
        else if (percentage >= 75) {{ color = '#7cb342'; }}
        else if (percentage >= 50) {{ color = '#ffc107'; }}
        else if (percentage >= 25) {{ color = '#fd7e14'; }}
        else {{ color = '#dc3545'; }}
        
        var borderColor = hasOpmerking ? '{OPMERKING_COLOR}' : 'white';
        var borderWidth = hasOpmerking ? '4px' : '3px';
        
        return L.divIcon({{
            html: '<div style="background-color:' + color + '; width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; border: ' + borderWidth + ' solid ' + borderColor + '; box-shadow: 0 2px 5px rgba(0,0,0,0.3);"><span style="color: white; font-weight: bold; font-size: 14px;">' + total + '</span></div>',
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
            'disableClusteringAtZoom': 18,
            'spiderfyOnMaxZoom': True,
            'showCoverageOnHover': False
        }
    )

    skipped_addresses = []
    added_count = 0
    opmerking_count = 0
    location_counts = {}

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
            
            # --- FIXED INDENTATION START ---
            is_done = str(row['Afgevinkt']).strip().lower() == 'ja'

            # Check voor opmerkingen
            has_opmerking = False
            opmerkingen = ""
            if 'Opmerkingen' in row and row['Opmerkingen']:
                opmerking_text = str(row['Opmerkingen']).strip()
                if opmerking_text and opmerking_text.lower() != 'nan':
                    has_opmerking = True
                    opmerking_count += 1
                    opmerking_text = opmerking_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    opmerkingen = f"<br><hr style='margin: 8px 0;'><b>💬 Opmerkingen:</b><br><i>{opmerking_text}</i>"

            kleur = OPMERKING_COLOR if has_opmerking else ('#28a745' if is_done else '#dc3545')

            popup_html = f"""
                <div style='min-width: 150px; max-width: 300px; font-family: Arial, sans-serif;'>
                    <b style='font-size: 14px;'>{row['Adres']}</b><br>
                    <span style='font-size: 12px;'>Status: {'✅ Afgevinkt' if is_done else '❌ Niet afgevinkt'}</span>
                    {opmerkingen}
                </div>
            """

            # Offset logic
            loc_key = f"{lat:.6f},{lon:.6f}"
            marker_lat, marker_lon = lat, lon
            if loc_key in location_counts:
                location_counts[loc_key] += 1
                offset = location_counts[loc_key] * 0.00001
                marker_lat += offset
                marker_lon += offset
            else:
                location_counts[loc_key] = 0

            marker = folium.CircleMarker(
                location=[marker_lat, marker_lon],
                radius=7,
                popup=folium.Popup(popup_html, max_width=300),
                color='white',
                weight=1.5,
                fill=True,
                fillColor=kleur,
                fillOpacity=0.85
            )

            marker.options['done'] = is_done
            marker.options['hasOpmerking'] = has_opmerking
            marker.add_to(marker_cluster)
            added_count += 1
            # --- FIXED INDENTATION END ---
            
        except Exception as e:
            skipped_addresses.append(f"{row.get('Adres', 'Onbekend')} (fout: {e})")

    marker_cluster.add_to(m)

    # Save and Upload
    m.save(LOCAL_OUTPUT)
    with open(LOCAL_OUTPUT, "r", encoding='utf-8') as f:
        content = f.read()

    try:
        auth = Auth.Token(GITHUB_TOKEN)
        g = Github(auth=auth)
        repo = g.get_repo(REPO_NAME)
        contents = repo.get_contents(FILE_PATH_IN_REPO)
        repo.update_file(contents.path, f"Update: {added_count} markers", content, contents.sha)
        print(f"✅ Succes! {added_count} markers op de kaart.")
    except Exception as e:
        print(f"❌ GitHub fout: {e}")

if __name__ == '__main__':
    generate_interactive_map()
