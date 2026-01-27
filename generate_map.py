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

# GitHub Gegevens - gebruik environment variable in GitHub Actions
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
if not GITHUB_TOKEN:
    raise ValueError("GITHUB_TOKEN environment variable not set. Make sure to add MAP_UPDATE_TOKEN secret in GitHub Actions.")

REPO_NAME = "roadeen/wijkkaart"
FILE_PATH_IN_REPO = "index.html"

# Lokale output bestand
LOCAL_OUTPUT = "index.html"

# Kleur voor adressen met opmerkingen
OPMERKING_COLOR = '#9b59b6'  # Purple

# ----------------------------
# 2. Google Setup
# ----------------------------
def get_credentials():
    """Get credentials from environment variable or file"""
    # In GitHub Actions, credentials komen van secrets
    creds_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
    
    if creds_json:
        # GitHub Actions: parse JSON from environment variable
        creds_dict = json.loads(creds_json)
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        return ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        # Lokaal: gebruik credentials.json file
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        return ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)

def generate_interactive_map():
    start_time = time.time()
    
    # --- A. Data ophalen uit Google Sheets ---
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

    # --- Debug: Check duplicaten ---
    print(f"\n📊 Totaal adressen: {len(df)}")
    
    # Check of 'Opmerkingen' kolom bestaat
    if 'Opmerkingen' in df.columns:
        opmerkingen_count = df['Opmerkingen'].notna().sum()
        print(f"✅ 'Opmerkingen' kolom gevonden ({opmerkingen_count} adressen met opmerkingen)")
    else:
        print("⚠️ Geen 'Opmerkingen' kolom gevonden in de data")
    
    coord_counts = df.groupby(['lat', 'lon']).size()
    duplicates = coord_counts[coord_counts > 1]
    if len(duplicates) > 0:
        print(f"⚠️ {len(duplicates)} locaties met meerdere adressen")

    # --- B. Kaart initialiseren ---
    print(f"\n🗺️ Kaart bouwen voor {len(df)} adressen...")
    
    m = folium.Map(
        location=[df['lat'].mean(), df['lon'].mean()], 
        zoom_start=16, 
        tiles='cartodbpositron'
    )

    LocateControl(auto_start=False, flyTo=True).add_to(m)

    # Custom icon function met paarse border voor opmerkingen
    icon_create_function = f"""
    function(cluster) {{
        var markers = cluster.getAllChildMarkers();
        var total = markers.length;
        var done = 0;
        var hasOpmerking = false;
        
        // Check voor opmerkingen en tel done markers
        markers.forEach(function(marker) {{
            if (marker.options.hasOpmerking) {{
                hasOpmerking = true;
            }}
            if (marker.options.done) {{
                done++;
            }}
        }});
        
        // Kleur op basis van voltooiingspercentage
        var percentage = (done / total) * 100;
        var color;
        
        if (percentage === 100) {{
            color = '#28a745';  // Donkergroen: 100% klaar
        }} else if (percentage >= 75) {{
            color = '#7cb342';  // Lime groen: 75-99% klaar (was #5cb85c)
        }} else if (percentage >= 50) {{
            color = '#ffc107';  // Geel: 50-74% klaar
        }} else if (percentage >= 25) {{
            color = '#fd7e14';  // Oranje: 25-49% klaar
        }} else {{
            color = '#dc3545';  // Rood: 0-24% klaar
        }}
        
        // Border kleur: paars als er opmerkingen zijn, anders wit
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
                skipped_addresses.append(f"{row['Adres']} (lat={lat}, lon={lon})")
                continue
            
            if not (50.5 <= lat <= 53.7 and 3.0 <= lon <= 7.5):
                skipped_addresses.append(f"{row['Adres']} (buiten Nederland: lat={lat}, lon={lon})")
                continue
            
            is_done = str(row['Afgevinkt']).strip().lower() == 'ja'
            
            # Check voor opmerkingen
            has_opmerking = False
            opmerkingen = ""
            if 'Opmerkingen' in row and row['Opmerkingen']:
                opmerking_text = str(row['Opmerkingen']).strip()
                if opmerking_text and opmerking_text.lower() != 'nan':
                    has_opmerking = True
                    opmerking_count += 1
                    # Escape HTML special characters
                    opmerking_text = opmerking_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    opmerkingen = f"<br><hr style='margin: 8px 0;'><b>💬 Opmerkingen:</b><br><i>{opmerking_text}</i>"
            
            # Bepaal kleur: paars als opmerking, anders groen/rood
            if has_opmerking:
                kleur = OPMERKING_COLOR
            else:
                kleur = '#28a745' if is_done else '#dc3545'
            
                # Bereken popup VOOR we de offset toepassen
                popup_html = f"""
                    <div style='min-width: 150px; max-width: 300px; font-family: Arial, sans-serif; word-wrap: break-word; overflow-wrap: break-word;'>
                        <b style='font-size: 14px;'>{row['Adres']}</b><br>
                        <span style='font-size: 12px;'>Status: {'✅ Afgevinkt' if is_done else '❌ Niet afgevinkt'}</span>
                        {opmerkingen}
                    </div>
                """

                # Pas offset toe voor overlappende markers
                loc_key = f"{lat:.6f},{lon:.6f}"
                marker_lat = lat
                marker_lon = lon

                if loc_key in location_counts:
                    location_counts[loc_key] += 1
                    offset = location_counts[loc_key] * 0.00001
                    marker_lat += offset
                    marker_lon += offset
                else:
                    location_counts[loc_key] = 0

                # Voeg marker toe met properties voor cluster kleuring
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
            
            # Voeg custom properties toe
            marker.options['done'] = is_done
            marker.options['hasOpmerking'] = has_opmerking
            
            marker.add_to(marker_cluster)
            
            added_count += 1
            
        except (ValueError, TypeError, KeyError) as e:
            skipped_addresses.append(f"{row.get('Adres', 'Onbekend adres')} (fout: {e})")

    marker_cluster.add_to(m)

    if skipped_addresses:
        print(f"\n⚠️ {len(skipped_addresses)} adressen overgeslagen")
    
    print(f"✅ {added_count} markers toegevoegd aan kaart")
    print(f"🟣 {opmerking_count} adressen met opmerkingen (paarse border)")

    # --- C. Opslaan ---
    print(f"\n💾 Kaart opslaan als '{LOCAL_OUTPUT}'...")
    m.save(LOCAL_OUTPUT)
    
    with open(LOCAL_OUTPUT, "r", encoding='utf-8') as f:
        content = f.read()

    # --- D. Upload naar GitHub ---
    print(f"\n⬆️ Uploaden naar GitHub...")
    socket.setdefaulttimeout(120)
    
    try:
        auth = Auth.Token(GITHUB_TOKEN)
        g = Github(auth=auth)
        repo = g.get_repo(REPO_NAME)
        
        try:
            contents = repo.get_contents(FILE_PATH_IN_REPO)
            repo.update_file(
                contents.path, 
                f"Auto-update: {added_count} adressen [{time.strftime('%Y-%m-%d %H:%M')}]", 
                content, 
                contents.sha
            )
            print("✅ Website succesvol bijgewerkt!")
        except Exception:
            repo.create_file(FILE_PATH_IN_REPO, "Initial upload", content)
            print("✨ Nieuwe website aangemaakt!")

    except Exception as e:
        print(f"❌ GitHub API fout: {e}")
        raise  # Re-raise zodat GitHub Actions het als failure ziet
    
    print(f"\n🚀 Klaar! Duur: {int(time.time() - start_time)} seconden.")

if __name__ == '__main__':
    generate_interactive_map()
