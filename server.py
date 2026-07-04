import base64
import glob
import io
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import qrcode
from flask import Flask, render_template, request
from flask_sock import Sock
from PIL import Image
from werkzeug.utils import secure_filename

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


active_cameras = {}  # ws -> metadata (e.g., {'enabled': True})


@app.route('/cameras')
def get_cameras():
    return json.dumps([{
        'id': str(id(ws)),
        'enabled': meta['enabled']
    } for ws, meta in active_cameras.items()])


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload_chunk', methods=['POST'])
def upload_chunk():
    id = secure_filename(request.form['id'])
    prefix = re.sub(r'\W+', '', request.form['prefix'].replace(" ",
                                                               "_")).lower()
    timestamp = secure_filename(request.form['timestamp'])
    file = request.files['chunk']

    # Create subfolder for the recording session
    os.makedirs(timestamp, exist_ok=True)

    filename = os.path.join(timestamp, f"{prefix}_{id}.webm")
    # 'ab' mode opens the file for appending in binary mode
    with open(filename, 'ab') as f:
        shutil.copyfileobj(file.stream, f)
    return "OK", 200


@app.route('/upload_snapshot', methods=['POST'])
def upload_snapshot():
    session_id = secure_filename(request.form['session_id'])
    file = request.files['snapshot']

    os.makedirs('snapshots', exist_ok=True)
    filename = os.path.join(
        'snapshots', f"snapshot_{session_id}_{uuid.uuid4().hex[:6]}.jpg")
    file.save(filename)

    # Check if we have all images
    if session_id in pending_snapshots:
        data = pending_snapshots[session_id]
        files = glob.glob(
            os.path.join('snapshots', f"snapshot_{session_id}_*.jpg"))

        if len(files) >= data['expected']:
            # All received, trigger processing
            pending_snapshots.pop(session_id)
            threading.Thread(target=process_grid,
                             args=(session_id, data['initiator_ws'])).start()
    return "OK", 200


# Maps session_id -> { 'expected': int, 'initiator_ws': ws }
pending_snapshots = {}


def convert_videos():
    # Convert all webm to mp4
    for webm_file in Path('.').rglob('*.webm'):
        mp4_file = webm_file.with_suffix('.mp4')
        subprocess.run([
            'ffmpeg', '-y', '-i',
            str(webm_file), '-r', '30', '-crf', '15', '-preset', 'ultrafast',
            '-b:a', '128k',
            str(mp4_file)
        ])
        webm_file.unlink()  # removes the original

    # Create grids for all folders that have mp4s and no grid.mp4
    for folder in Path('.').iterdir():
        if folder.is_dir() and folder.name not in ['.', '..', 'snapshots']:
            if (folder / 'grid.mp4').exists():
                continue

            mp4_files = sorted(list(folder.glob('*.mp4')))
            if len(mp4_files) < 2:
                continue

            n = len(mp4_files)
            cols = math.ceil(math.sqrt(n))

            # Build inputs and layout
            inputs = []
            for f in mp4_files:
                inputs.extend(['-i', str(f)])

            # Prepare inputs with scaling to standard 640x360 size
            filter_parts = []
            video_labels = []
            audio_labels = []

            for i in range(n):
                # Scale each video to 640x360 (padding with black to keep aspect ratio if needed) and normalize pixel format
                filter_parts.append(
                    f"[{i}:v]scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2:color=black,format=yuv420p,setsar=1[v{i}]"
                )
                video_labels.append(f"[v{i}]")
                audio_labels.append(f"[{i}:a]")

            # Layout string for xstack (since all are now 640x360, we can use absolute coordinates)
            layout_parts = []
            for i in range(n):
                c = i % cols
                r = i // cols
                layout_parts.append(f"{640 * c}_{360 * r}")
            layout = "|".join(layout_parts)

            video_filter = f"{''.join(video_labels)}xstack=inputs={n}:layout={layout}[v]"
            audio_filter = f"{''.join(audio_labels)}amix=inputs={n}:duration=longest[a]"
            filter_complex = ";".join(filter_parts +
                                      [video_filter, audio_filter])

            cmd = ['ffmpeg', '-y'] + inputs + [
                '-filter_complex', filter_complex, '-map', '[v]', '-map',
                '[a]', '-preset', 'ultrafast',
                str(folder / 'grid.mp4')
            ]
            subprocess.run(cmd)
            print(f"Created grid.mp4 in {folder}")


def process_grid(session_id, initiator_ws):
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
    active_cameras[ws] = {'enabled': False}
    print(f"Camera connected. Total cameras: {len(active_cameras)}")
    try:
        while True:
            data = ws.receive()
            if data is None: break

            # We only handle string (JSON) commands now
            if isinstance(data, str):
                cmd = json.loads(data)

                if cmd.get('action') == 'camera_started':
                    active_cameras[ws]['enabled'] = True
                    print(
                        f"Camera enabled. Total enabled: {sum(c['enabled'] for c in active_cameras.values())}"
                    )

                if cmd.get('action') == 'stop_all':
                    active_cameras[ws]['enabled'] = False

                if cmd.get('action') == 'snapshot':
                    session_id = uuid.uuid4().hex

                    # Calculate how many enabled cameras we expect
                    enabled_count = sum(1 for c in active_cameras.values()
                                        if c['enabled'])

                    if enabled_count > 0:
                        pending_snapshots[session_id] = {
                            'expected': enabled_count,
                            'initiator_ws': ws
                        }

                        # Broadcast to clients
                        for conn in list(active_cameras.keys()):
                            try:
                                conn.send(
                                    json.dumps({
                                        'action': 'snapshot',
                                        'session_id': session_id
                                    }))
                            except Exception:
                                pass
                    else:
                        print("No enabled cameras to take snapshot.")

                if cmd.get('action') == 'convert':
                    # Run conversion in background
                    threading.Thread(target=convert_videos).start()
                    print("Conversion started", file=sys.stderr)

                if cmd.get('action') == 'get_time':
                    ws.send(
                        json.dumps({
                            'action': 'time_sync',
                            'server_time': int(time.time() * 1000)
                        }))
                    continue

                # Handle 'start' specifically to inject a sync timestamp
                if cmd.get('action') == 'start':
                    start_at = int(time.time() * 1000) + 1000
                    data = json.dumps({
                        'action': 'start',
                        'start_at': start_at
                    })
                    for conn in list(active_cameras.keys()):
                        try:
                            conn.send(data)
                        except Exception:
                            pass
                    continue

                # Broadcast command to everyone
                for conn in list(active_cameras.keys()):
                    try:
                        conn.send(data)
                    except Exception:
                        pass

    finally:
        del active_cameras[ws]
        print(f"Camera disconnected. Total cameras: {len(active_cameras)}")


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
