from flask import Flask, request, jsonify, send_from_directory
import subprocess
import os
import json
import re

app = Flask(__name__, static_folder='static')

# Path to your downloader.py
DOWNLOADER_SCRIPT = os.path.join(os.path.dirname(__file__), 'downloader.py')
DOWNLOADS_DIR = os.path.join(os.path.dirname(__file__), 'downloads')

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/download', methods=['POST'])
def download():
    data = request.get_json()
    url = data.get('url', '').strip()

    if not url:
        return jsonify({'success': False, 'error': 'No URL provided'}), 400

    try:
        # Run the downloader script with the URL as input
        # The script may prompt for input, so we pass it via stdin
        process = subprocess.run(
            ['python', DOWNLOADER_SCRIPT],
            input=url + '\n',
            capture_output=True,
            text=True,
            timeout=300  # 5 minutes timeout
        )

        stdout = process.stdout
        stderr = process.stderr

        # Parse output to find the downloaded file name
        # (Adjust this based on your script's actual output format)
        file_match = re.search(r'Done! \d+/\d+ PDFs saved to (.+)', stdout)
        downloaded_file = None
        if file_match:
            # The script saves files in a folder; you may need to pick the latest file
            # For simplicity, assume it saves a single PDF in DOWNLOADS_DIR
            files = os.listdir(DOWNLOADS_DIR) if os.path.exists(DOWNLOADS_DIR) else []
            if files:
                # Get the most recently created PDF
                pdf_files = [f for f in files if f.endswith('.pdf')]
                if pdf_files:
                    pdf_files.sort(key=lambda x: os.path.getmtime(os.path.join(DOWNLOADS_DIR, x)), reverse=True)
                    downloaded_file = pdf_files[0]

        logs = stdout.splitlines() + stderr.splitlines()

        if process.returncode == 0 and downloaded_file:
            return jsonify({
                'success': True,
                'file': downloaded_file,
                'logs': logs
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Download failed. Check logs.',
                'logs': logs
            })

    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Download timed out'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/downloads/<filename>')
def serve_download(filename):
    return send_from_directory(DOWNLOADS_DIR, filename, as_attachment=True)

if __name__ == '__main__':
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    app.run(debug=True, port=5000)
