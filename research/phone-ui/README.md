# OpenBar Mobile Test Harness (`research/phone-ui`)

A zero-dependency (Python stdlib only) mobile web UI for testing the OpenBar Rust measurement engine on the **Xiaomi 14T Pro** (`rothko`, model `2407FPN8EG`).

> **Scope note:** This is a research/dogfooding harness under `research/phone-ui/`. It does **not** unlock M1 ([ADR-0008](../../docs/adr/0008-m0-go-no-go-and-m1-entry.md)) and does **not** reimplement calibration, filtering, or kinematics. Every analysis runs through the authoritative `openbar-cli analyze` and `openbar-cli render` commands.

## Two ways to run on your Xiaomi 14T Pro

### 1. Fully on-device in Termux (Offline in the gym)

 Using the [ADR-0009 Termux setup](../../docs/validation/PHONE_RUNTIME_BENCHMARK.md#phone-setup):

```bash
cargo build --locked --release -p openbar-cli
python research/phone-ui/phone_app.py --port 8080 --binary target/release/openbar-cli
```

Then open **`http://127.0.0.1:8080`** in Chrome on your Xiaomi 14T Pro:
1. Tap **Choose File** to record a lift with the camera or pick a clip from your gallery.
2. Tap **Load Seed Frame** (extracts the display-oriented frame with `ffprobe`/`ffmpeg` PTS alignment).
3. Tap the plate centre on the touch canvas and adjust the radius slider.
4. Select `--tracker csrt-gray`, `opencv-csrt` (via `research/opencv-tracking/track.py` -> `openbar-cli analyze --observations`, supporting full 1080p plate radii `> 127.75 px`), `template`, or `contrast`, and tap **Run OpenBar Analysis**.
5. Inspect upward rep velocity preview ([ADR-0012](../../docs/adr/0012-portable-rep-and-phase-metrics-contract.md)), stage timings against the `< 5.0 s` median / `< 8.0 s` max target ([ANDROID_LATENCY_EVIDENCE.md](../../docs/validation/ANDROID_LATENCY_EVIDENCE.md)), and the diagnostic SVG overlay ([ADR-0013](../../docs/adr/0013-video-playback-and-bar-path-overlay-contract.md)).

### 2. Over Home/Gym Wi-Fi from PC

```bash
cargo build --locked --release -p openbar-cli
python research/phone-ui/phone_app.py --host 0.0.0.0 --port 8080
```

Open `http://<your-pc-lan-ip>:8080` from Chrome on your Xiaomi 14T Pro.

## Tests

```bash
python -m unittest discover -v -s research/phone-ui/tests -p 'test_*.py'
```
