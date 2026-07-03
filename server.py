# /// script
# dependencies = [
#   "flask",
#   "flask-sock",
# ]
# ///

import logging
import uuid
import sys
from datetime import datetime
from flask import Flask, render_template_string
from flask_sock import Sock

# Silence Werkzeug logging
logging.getLogger('werkzeug').disabled = True

app = Flask(__name__)
app.logger.disabled = True
sock = Sock(app)

HTML_PAGE = """
<!DOCTYPE html>
<html>
<body>
    <video id="v" width="320" height="240" autoplay playsinline muted></video>
    <br>
    <button id="b">Start Streaming</button>
    <button id="stopBtn" disabled>Stop Streaming</button>
    <script>
        const v = document.getElementById('v');
        const b = document.getElementById('b');
        const stopBtn = document.getElementById('stopBtn');
        let ws;
        let recorder;

        b.onclick = async () => {
            const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
            v.srcObject = stream;
            recorder = new MediaRecorder(stream, { mimeType: 'video/webm' });
            
            const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
            ws = new WebSocket(`${protocol}://${location.host}/stream`);
            
            ws.onopen = () => {
                recorder.ondataavailable = (e) => {
                    if (e.data.size > 0 && ws.readyState === WebSocket.OPEN) {
                        ws.send(e.data);
                    }
                };
                recorder.start(500);
                b.disabled = true;
                stopBtn.disabled = false;
                b.innerText = "Streaming...";
            };
        };

        stopBtn.onclick = () => {
            if (recorder && recorder.state !== "inactive") recorder.stop();
            if (ws && ws.readyState === WebSocket.OPEN) ws.close();
            if (v.srcObject) v.srcObject.getTracks().forEach(t => t.stop());
            stopBtn.disabled = true;
            b.disabled = false;
            b.innerText = "Start Streaming";
        };
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_PAGE)

@sock.route('/stream')
def stream(ws):
    filename = f"stream_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.webm"
    print(f"Recording to {filename}", file=sys.stderr)
    try:
        with open(filename, 'ab') as f:
            while True:
                data = ws.receive()
                if data is None:
                    break
                f.write(data)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, threaded=True)
