# Stream Video

A lightweight, web-based video streaming and recording application. This project allows you to stream video from a browser, record the stream to the server, perform snapshots, and batch-convert recordings to MP4.

## Features

- **Browser-Based Streaming**: Capture video directly from your camera.
- **Recording Management**: Record video streams to the server, with automatic cleanup of old sessions.
- **Snapshot Grid**: Capture snapshots and automatically generate a grid image.
- **Batch Conversion**: Automatically convert `.webm` recordings to `.mp4` using `ffmpeg`.
- **Easy Access**: Automatically generates a QR code in the terminal for quick mobile device access.
- **Audio Control**: Configured with Auto Gain Control enabled and Noise Suppression/Echo Cancellation disabled for raw audio input.
- **Input Normalization**: Stream naming automatically normalizes to lowercase alphanumeric characters.
- **Screen Wake Lock**: Prevents the browser/screen from sleeping while the stream is active.

## Prerequisites

- **Python 3.x**
- **FFmpeg**: Required for video conversion (`sudo apt install ffmpeg` or `brew install ffmpeg`).

## Installation

1. Clone the repository.
2. Install the dependencies using your preferred package manager (this project uses `pyproject.toml` and `uv`):

```bash
# If using uv
uv sync

# Or using standard pip
pip install flask flask-sock pillow qrcode
```

## Running the Server

Start the application:

```bash
uv run server.py
python server.py
```

Upon startup, the terminal will display a URL and a QR code. Scan the QR code or navigate to the URL in your browser.

## Usage

1. **Start Camera**: Select your desired camera source and enter a name (e.g., "front-door"). The system will normalize this name automatically. Click "Start Camera".
2. **Recording**: Click "Start Recording" to begin streaming to the server. Your recordings will be saved in subfolders named by timestamp.
3. **Preview cameras**: Click "Preview cameras" to take a photo. When multiple snapshots are taken, the server can generate a grid preview.
4. **Conversion**: Click "Convert Recordings to MP4" to trigger the background FFmpeg process to batch-convert all `.webm` files into `.mp4` and remove the source files.

## Development Notes

- The server uses HTTPS with an ad-hoc SSL certificate. You may need to accept the "Insecure" warning in your browser to view the page.
- Audio settings are hardcoded in `assets/index.html` to enable Auto Gain Control while disabling Echo Cancellation and Noise Suppression.
- Device name inputs are sanitized using regex `\W+` in `server.py` to ensure file system safety.
