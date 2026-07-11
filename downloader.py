import re
import requests
from bs4 import BeautifulSoup
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import os
import glob

print("=" * 70)
print("🚀 GOOGLE DRIVE FILE DOWNLOADER (FULLY AUTOMATED)")
print("=" * 70)

# ============== CONFIGURATION ==============
DOWNLOAD_DIR = "downloaded_notes"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ============== STEP 1: SETUP CHROME ==============
print("\n🔧 Setting up Chrome driver...")

options = Options()
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-gpu")
options.add_argument("--window-size=1920,1080")

# Use your Chrome profile (keeps you logged in)
user_data_dir = os.path.expanduser("~") + "\\AppData\\Local\\Google\\Chrome\\User Data"
options.add_argument(f"user-data-dir={user_data_dir}")
options.add_argument("profile-directory=Default")

# Disable notifications
options.add_argument("--disable-notifications")
options.add_argument("--disable-popup-blocking")

# Download settings
prefs = {
    "download.default_directory": os.path.abspath(DOWNLOAD_DIR),
    "download.prompt_for_download": False,
    "download.directory_upgrade": True,
    "safebrowsing.enabled": True,
}
options.add_experimental_option("prefs", prefs)

# Start Chrome
driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=options
)
print("✅ Chrome driver started")

# ============== STEP 2: CHECK LOGIN ==============
print("\n🔐 Checking Google Drive login...")
driver.get("https://drive.google.com")
time.sleep(3)

if "accounts.google.com" in driver.current_url:
    print("⚠️ Please log in to Google Drive in the browser window...")
    print("⏳ Waiting for login (you have 2 minutes)...")
    WebDriverWait(driver, 120).until(
        EC.url_contains("drive.google.com")
    )
    print("✅ Login successful!")
else:
    print("✅ Already logged in!")

# ============== STEP 3: SCRAPE INDEX PAGE ==============
print("\n📥 Fetching index page...")

response = requests.get(
    "https://www.downloadclassnotes.com/english-notes-for-5th-class-for-punjab-schools-colleges/",
    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
)
soup = BeautifulSoup(response.text, 'html.parser')

subpage_urls = []
for tag in soup.find_all('a'):
    href = tag.get('href')
    if href:
        if href.startswith('/'):
            href = "https://www.downloadclassnotes.com" + href
        if "downloadclassnotes.com" in href and "unit" in href and "english" in href.lower():
            subpage_urls.append(href)

print(f"✅ Found {len(subpage_urls)} unit pages")

# ============== STEP 4: SCAN FOR FILE IDs ==============
print("\n🔍 Scanning for file IDs...")

file_ids = set()
patterns = [
    r'/d/([a-zA-Z0-9_-]{25,})',
    r'id=([a-zA-Z0-9_-]{25,})',
    r'https://drive\.google\.com/file/d/([a-zA-Z0-9_-]{25,})',
]

for idx, url in enumerate(sorted(subpage_urls), 1):
    print(f"  [{idx}/{len(subpage_urls)}] Scanning: {url.split('/')[-2]}...")
    
    try:
        driver.get(url)
        time.sleep(2)
        content = driver.page_source
        
        found = set()
        for pattern in patterns:
            found.update(re.findall(pattern, content))
        
        file_ids.update(found)
        if found:
            print(f"    ✅ Found {len(found)} ID(s): {', '.join(list(found)[:3])}...")
        else:
            print("    ⚠️ No IDs found")
            
    except Exception as e:
        print(f"    ❌ Error: {e}")

print(f"\n✅ Total unique file IDs found: {len(file_ids)}")

if not file_ids:
    print("\n❌ No file IDs found. Exiting...")
    driver.quit()
    exit()

# ============== STEP 5: DOWNLOAD FILES ==============
print(f"\n⬇️ Downloading {len(file_ids)} files...")

# Get cookies from browser for requests
cookies = {c['name']: c['value'] for c in driver.get_cookies()}
session = requests.Session()
session.cookies.update(cookies)
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
})

success_count = 0
failed_files = []

for idx, file_id in enumerate(sorted(file_ids), 1):
    print(f"\n[{idx}/{len(file_ids)}] 📥 Processing: {file_id[:15]}...")
    
    output_path = os.path.join(DOWNLOAD_DIR, f"unit_{idx}_{file_id}.pdf")
    
    # === METHOD 1: HTTP with cookies ===
    try:
        url = f"https://drive.google.com/uc?export=download&id={file_id}"
        print("  🔄 Trying HTTP download...")
        
        response = session.get(url, allow_redirects=False)
        
        # Handle confirmation page
        if "confirm" in response.text:
            confirm_match = re.search(r'confirm=([^&]+)', response.text)
            if confirm_match:
                confirm_token = confirm_match.group(1)
                download_url = f"https://drive.google.com/uc?export=download&confirm={confirm_token}&id={file_id}"
                response = session.get(download_url)
        
        # Check if we got a valid PDF
        if response.status_code == 200:
            # Check headers or first few bytes
            content_type = response.headers.get('Content-Type', '')
            if 'application/pdf' in content_type or 'application/octet-stream' in content_type:
                with open(output_path, 'wb') as f:
                    f.write(response.content)
                
                # Verify it's a real PDF
                with open(output_path, 'rb') as f:
                    if f.read(5) == b'%PDF-':
                        print("  ✅ HTTP download successful (valid PDF)")
                        success_count += 1
                        continue
                    else:
                        os.remove(output_path)
                        print("  ⚠️ Downloaded file is not a valid PDF, trying Selenium...")
            elif 'text/html' not in content_type:
                # Might still be a valid file
                with open(output_path, 'wb') as f:
                    f.write(response.content)
                print("  ✅ HTTP download successful (binary file)")
                success_count += 1
                continue
            else:
                print("  ⚠️ HTTP returned HTML page, trying Selenium...")
        else:
            print(f"  ⚠️ HTTP status {response.status_code}, trying Selenium...")
            
    except Exception as e:
        print(f"  ⚠️ HTTP failed: {str(e)[:50]}")

    # === METHOD 2: SELENIUM BROWSER DOWNLOAD ===
    try:
        print("  🔄 Trying Selenium browser download...")
        
        # Open the view page
        view_url = f"https://drive.google.com/file/d/{file_id}/view"
        driver.get(view_url)
        time.sleep(3)
        
        # Click download button
        try:
            download_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//*[@aria-label='Download']"))
            )
            download_btn.click()
            print("  📥 Clicked download button")
        except:
            # Alternative: direct download URL
            print("  🔄 Using direct download URL...")
            driver.get(f"https://drive.google.com/uc?export=download&id={file_id}")
        
        time.sleep(2)
        
        # Handle "Download anyway" if it appears
        try:
            confirm_btn = driver.find_element(By.XPATH, "//*[contains(text(), 'Download anyway')]")
            confirm_btn.click()
            print("  ✅ Clicked 'Download anyway'")
        except:
            pass
        
        # Wait for download
        print("  ⏳ Waiting for download to complete...")
        time.sleep(5)
        
        # Find the downloaded file
        files = glob.glob(os.path.join(DOWNLOAD_DIR, "*.pdf"))
        files.sort(key=os.path.getctime)
        
        if files:
            latest_file = files[-1]
            # Check if it's a valid PDF
            with open(latest_file, 'rb') as f:
                if f.read(5) == b'%PDF-':
                    # Rename to our desired name
                    if latest_file != output_path:
                        os.rename(latest_file, output_path)
                    print("  ✅ Selenium download successful (valid PDF)")
                    success_count += 1
                    continue
                else:
                    os.remove(latest_file)
                    print("  ⚠️ Selenium downloaded invalid file")
        else:
            print("  ⚠️ No file found after download")
            
    except Exception as e:
        print(f"  ❌ Selenium failed: {str(e)[:50]}")

    # If we get here, both methods failed
    failed_files.append(file_id)
    print("  ❌ All download methods failed")

# ============== STEP 6: VERIFY AND REPORT ==============
print("\n" + "=" * 70)
print("📊 DOWNLOAD REPORT")
print("=" * 70)

# Count and verify downloads
valid_pdfs = 0
total_size = 0

print("\n🔍 Verifying downloaded files:")
for f in sorted(os.listdir(DOWNLOAD_DIR)):
    if f.endswith('.pdf'):
        path = os.path.join(DOWNLOAD_DIR, f)
        size = os.path.getsize(path) / 1024  # KB
        
        with open(path, 'rb') as file:
            header = file.read(5)
            if header == b'%PDF-':
                valid_pdfs += 1
                total_size += size
                status = "✅ VALID"
            else:
                status = "❌ INVALID"
        
        print(f"  {status} {f} ({size:.2f} KB)")

print("\n" + "=" * 70)
print("📊 FINAL SUMMARY")
print("=" * 70)
print(f"  📁 Total files found: {len(file_ids)}")
print(f"  ✅ Successfully downloaded: {success_count}")
print(f"  ✅ Valid PDFs: {valid_pdfs}")
print(f"  ❌ Failed: {len(failed_files)}")
print(f"  📦 Total size: {total_size:.2f} KB")
print(f"  📂 Saved to: {os.path.abspath(DOWNLOAD_DIR)}")

if failed_files:
    print(f"\n⚠️ Failed files ({len(failed_files)}):")
    for fid in failed_files:
        print(f"    - https://drive.google.com/uc?id={fid}")

if valid_pdfs == len(file_ids):
    print("\n🎉 ALL FILES DOWNLOADED SUCCESSFULLY!")
elif valid_pdfs > 0:
    print(f"\n⚠️ Downloaded {valid_pdfs} out of {len(file_ids)} files.")
    print("   You can manually download the failed ones from the links above.")
else:
    print("\n❌ No valid PDFs were downloaded.")
    print("   Possible reasons:")
    print("   1. Files require special permissions")
    print("   2. Need to be logged into the correct Google account")
    print("   3. Files may have been moved or deleted")

# ============== STEP 7: CLEANUP ==============
driver.quit()
print("\n🧹 Cleanup complete")
print("\n✨ Process complete!")