# dependencies = [
#   "flask",
#   "flask-sock",
#   "pyOpenSSL",
# ]
# ///

import base64
import glob
import io
import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
import uuid

import qrcode
from flask import Flask, jsonify, render_template_string, request
from flask_sock import Sock
from PIL import Image

# Silence Werkzeug logging
# logging.getLogger('werkzeug').disabled = True
app = Flask(__name__)
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

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; }
        html, body {
            overflow: hidden;
            width: 100%;
            height: 100%;
            margin: 0;
            padding: 0;
        }
        body { 
            font-family: sans-serif; 
            display: flex; 
            flex-direction: row; 
            height: 100vh; 
        }
        @media (max-width: 600px) {
            body { flex-direction: column; }
            #controls { width: 100% !important; height: auto; flex: 0 0 auto; }
        }
        #controls { 
            width: 300px; 
            max-width: 100%;
            padding: 15px; 
            display: flex; 
            flex-direction: column; 
            gap: 10px; 
            overflow: hidden; 
            background: #f0f0f0;
        }
        #video-container { 
            flex: 1; 
            display: flex; 
            align-items: center; 
            justify-content: center; 
            background: #000; 
        }
        video { 
            width: 100%; 
            height: 100%; 
            object-fit: contain; 
        }
        button, select, input { width: 100%; padding: 10px; font-size: 16px; }
    </style>
</head>
<body>
    <div id="controls">
        <select id="videoSource"></select>
        <input type="text" id="prefixInput" placeholder="Enter file prefix...">
        <button id="cameraToggleBtn">Start Camera</button>
        <button id="toggleBtn" disabled>Start Recording</button>
        <button id="convertBtn">Convert Recordings to MP4</button>
        <button id="snapshotBtn">Snapshot</button>
        <img id="gridOverlay" style="display:none; position:absolute; top:0; left:0; width:100%; height:100%; z-index:10; object-fit: contain; background: black;">
    </div>
    <div id="video-container">
        <video id="v" autoplay playsinline muted></video>
    </div>
    
    <script>
        const v = document.getElementById('v');
        const cameraToggleBtn = document.getElementById('cameraToggleBtn');
        const toggleBtn = document.getElementById('toggleBtn');
        const videoSource = document.getElementById('videoSource');
        const convertBtn = document.getElementById('convertBtn');
        const snapshotBtn = document.getElementById('snapshotBtn');
        const gridOverlay = document.getElementById('gridOverlay');
        let ws;
        let recorder;
        let wakeLock = null;
    
        const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
        ws = new WebSocket(`${protocol}://${location.host}/stream`);

        async function requestWakeLock() {
            try {
                wakeLock = await navigator.wakeLock.request('screen');
                console.log('Wake Lock active');
                wakeLock.addEventListener('release', () => {
                    console.log('Wake Lock released');
                    wakeLock = null;
                });
            } catch (err) {
                console.error(`Wake Lock error: ${err.name}, ${err.message}`);
            }
        }

        document.addEventListener('visibilitychange', async () => {
            if (wakeLock !== null && document.visibilityState === 'visible') {
                await requestWakeLock();
            }
        });

        ws.onmessage = (event) => {
            if (typeof event.data === 'string') {
                const cmd = JSON.parse(event.data);
                if (cmd.action === 'start') {
                    startLocalRecording();
                    toggleBtn.textContent = 'Stop Recording';
                }
                if (cmd.action === 'stop') {
                    stopLocalRecording();
                    toggleBtn.textContent = 'Start Recording';
                }
                if (cmd.action === 'snapshot') {
                    takeSnapshot(cmd.session_id);
                }
                if (cmd.action === 'show_grid') {
                    gridOverlay.src = 'data:image/jpeg;base64,' + cmd.image_data;
                    gridOverlay.style.display = 'block';
                    v.style.display = 'none';
                    setTimeout(() => {
                        gridOverlay.style.display = 'none';
                        v.style.display = 'block';
                    }, 4000);
                }
                if (cmd.action === 'stop_all') {
                    stopLocalRecording();
                    if (v.srcObject) {
                        v.srcObject.getTracks().forEach(track => track.stop());
                        v.srcObject = null;
                    }
                    if (wakeLock !== null) {
                        wakeLock.release()
                            .then(() => { wakeLock = null; console.log('Wake Lock released'); })
                            .catch((err) => console.error(`Wake Lock release error: ${err.name}, ${err.message}`));
                    }
                    toggleBtn.textContent = 'Start Recording';
                    toggleBtn.disabled = true;
                    cameraToggleBtn.textContent = 'Start Camera';
                }
            }
        };

        async function refreshDevices() {
            const devices = await navigator.mediaDevices.enumerateDevices();
            const videoDevices = devices.filter(d => d.kind === 'videoinput');
            videoSource.innerHTML = '';
            videoDevices.forEach(d => {
                const opt = document.createElement('option');
                opt.value = d.deviceId;
                opt.text = d.label || `Camera ${videoSource.length + 1}`;
                videoSource.appendChild(opt);
            });
        }

        // Initialize: Get Camera Access
        cameraToggleBtn.onclick = async () => {
            if (cameraToggleBtn.textContent === 'Start Camera') {
                try {
                    const stream = await navigator.mediaDevices.getUserMedia({ 
                        video: { 
                            width: { ideal: 1280 }, 
                            height: { ideal: 720 },
                            aspectRatio: { ideal: 16/9 }
                        }, 
                        audio: true 
                    });
                    v.srcObject = stream;
                    await refreshDevices();
                    await requestWakeLock();
                    toggleBtn.disabled = false;
                    cameraToggleBtn.textContent = 'Stop All Cameras';
                } catch (err) {
                    alert("Camera access denied or resolution not supported: " + err);
                }
            } else {
                ws.send(JSON.stringify({action: 'stop_all'}));
            }
        };

        videoSource.onchange = async () => {
            const tracks = v.srcObject.getTracks();
            tracks.forEach(track => track.stop());
            
            try {
                const newStream = await navigator.mediaDevices.getUserMedia({ 
                    video: { 
                        deviceId: { exact: videoSource.value },
                        width: { ideal: 1280 }, 
                        height: { ideal: 720 },
                        aspectRatio: { ideal: 16/9 }
                    }, 
                    audio: true 
                });
                v.srcObject = newStream;
            } catch (err) {
                alert("Failed to switch camera: " + err);
            }
        };

        // Control buttons
        toggleBtn.onclick = () => {
            const action = toggleBtn.textContent.includes('Start') ? 'start' : 'stop';
            ws.send(JSON.stringify({action: action}));
        };
        convertBtn.onclick = () => ws.send(JSON.stringify({action: 'convert'}));
        snapshotBtn.onclick = () => ws.send(JSON.stringify({action: 'snapshot'}));

        let uploadQueue = Promise.resolve();

        function takeSnapshot(sessionId) {
            const canvas = document.createElement('canvas');
            canvas.width = v.videoWidth;
            canvas.height = v.videoHeight;
            canvas.getContext('2d').drawImage(v, 0, 0);
            canvas.toBlob(blob => {
                const formData = new FormData();
                formData.append('snapshot', blob, 'snapshot.jpg');
                formData.append('session_id', sessionId);
                fetch('/upload_snapshot', { method: 'POST', body: formData });
            }, 'image/jpeg');
        }

        function startLocalRecording() {
            const recordingId = uuidv4(); 
            const prefix = document.getElementById('prefixInput').value || 'stream';
            const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
            recorder = new MediaRecorder(v.srcObject, { 
                mimeType: 'video/webm',
                videoBitsPerSecond: 4000000 // Reduced to 4 Mbps for stability
            });
            
            recorder.onerror = (e) => {
                alert("Recorder error: " + e.error);
                stopLocalRecording();
            };
            
            recorder.ondataavailable = (e) => {
                if (e.data.size > 0) {
                    const formData = new FormData();
                    formData.append('id', recordingId);
                    formData.append('prefix', prefix);
                    formData.append('timestamp', timestamp);
                    formData.append('chunk', e.data);
                    
                    uploadQueue = uploadQueue.then(() => 
                        fetch('/upload_chunk', { method: 'POST', body: formData })
                    ).catch(err => console.error("Upload failed", err));
                }
            };
            
            recorder.start(2000); 
        }

        function stopLocalRecording() {
            if (recorder && recorder.state !== "inactive") recorder.stop();
        }

        function uuidv4() {
            return ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g, c =>
                (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16)
            );
        }
    </script>
</body>
</html>
"""


@app.route('/')
def index():
    return render_template_string(HTML_PAGE)


@app.route('/upload_chunk', methods=['POST'])
def upload_chunk():
    id = request.form['id']
    prefix = request.form['prefix']
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
    filename = os.path.join('snapshots', f"snapshot_{session_id}_{uuid.uuid4().hex[:6]}.jpg")
    file.save(filename)
    return "OK", 200


def process_grid(session_id, initiator_ws):
    time.sleep(2)  # Wait for uploads
    files = glob.glob(os.path.join("snapshots", f"snapshot_{session_id}_*.jpg"))
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
