import base64
import glob
import io
import json
import math
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid

import qrcode
from flask import Flask, render_template, request
from flask_sock import Sock
from PIL import Image

# Silence Werkzeug logging
# logging.getLogger('werkzeug').disabled = True
app = Flask(__name__, template_folder='assets')
# app.logger.disabled = True
sock = Sock(app)


# Helper to get local IP
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(('10.254.254.254', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP


def print_qr_code(url):
    qr = qrcode.QRCode(version=1, box_size=1, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    print(f"\nScan this QR code to access the stream:\n{url}\n")
    qr.print_ascii()
    print("\n")


connections = set()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload_chunk', methods=['POST'])
def upload_chunk():
    id = request.form['id']
    prefix = re.sub(r'\W+', '', request.form['prefix'].replace(" ",
                                                               "_")).lower()
    timestamp = request.form['timestamp']
    file = request.files['chunk']

    # Create subfolder for the recording session
    os.makedirs(timestamp, exist_ok=True)

    filename = os.path.join(timestamp, f"{prefix}_{id}.webm")
    # 'ab' mode opens the file for appending in binary mode
    with open(filename, 'ab') as f:
        f.write(file.read())
    return "OK", 200


@app.route('/upload_snapshot', methods=['POST'])
def upload_snapshot():
    session_id = request.form['session_id']
    file = request.files['snapshot']

    os.makedirs('snapshots', exist_ok=True)
    filename = os.path.join(
        'snapshots', f"snapshot_{session_id}_{uuid.uuid4().hex[:6]}.jpg")
    file.save(filename)
    return "OK", 200


def process_grid(session_id, initiator_ws):
    time.sleep(2)  # Wait for uploads
    files = glob.glob(os.path.join("snapshots",
                                   f"snapshot_{session_id}_*.jpg"))
    if not files: return

    images = [Image.open(f) for f in files]
    n = len(images)
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)

    # Simple resize for uniform grid
    w, h = images[0].size
    grid_img = Image.new('RGB', (w * cols, h * rows))

    for i, img in enumerate(images):
        grid_img.paste(img, ((i % cols) * w, (i // cols) * h))

    # Save to buffer
    buffered = io.BytesIO()
    grid_img.save(buffered, format="JPEG")
    img_str = base64.b64encode(buffered.getvalue()).decode()

    # Send only to the initiator
    try:
        initiator_ws.send(
            json.dumps({
                'action': 'show_grid',
                'image_data': img_str
            }))
    except Exception as e:
        print(f"Failed to send grid to initiator: {e}")

    # Cleanup
    for f in files:
        os.remove(f)


@sock.route('/stream')
def stream(ws):
    connections.add(ws)
    try:
        while True:
            data = ws.receive()
            if data is None: break

            # We only handle string (JSON) commands now
            if isinstance(data, str):
                cmd = json.loads(data)
                if cmd.get('action') == 'snapshot':
                    session_id = uuid.uuid4().hex
                    # Broadcast to clients
                    for conn in connections:
                        conn.send(
                            json.dumps({
                                'action': 'snapshot',
                                'session_id': session_id
                            }))
                    # Start processor
                    threading.Thread(target=process_grid,
                                     args=(session_id, ws)).start()

                if cmd.get('action') == 'convert':
                    # Run conversion in background
                    subprocess.Popen([
                        'bash', '-c',
                        r'find . -name "*.webm" -exec ffmpeg -y -i {} -r 30 -crf 15 -b:a 128k {}.mp4 \; -exec rm {} \;'
                    ])
                    print("Conversion started", file=sys.stderr)

                # Broadcast command to everyone
                for conn in connections:
                    conn.send(data)

    finally:
        connections.remove(ws)


if __name__ == '__main__':
    host = '0.0.0.0'
    port = 5001
    local_ip = get_local_ip()
    url = f"https://{local_ip}:{port}"
    print_qr_code(url)
    app.run(host=host,
            port=port,
            threaded=True,
            ssl_context='adhoc',
            debug=True)
