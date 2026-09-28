# Browser source for Restreamer

This service replaces an OBS Browser Source. It opens a persistent Chromium
session, provides an interactive control screen, and sends its video and audio
to one Restreamer RTMP input. It runs beside Restreamer in the same Portainer
stack, with no OBS process to manage.

1. In Restreamer, enable the internal RTMP server in System Settings > RTMP,
   then create a new RTMP input channel. Copy its RTMP publish URL. The RTMP
   port does not need to be published to the internet: both services share a
   Docker network.
2. Add the service and volume from `portainer-service.example.yaml` to your
   existing Portainer stack. Preserve the existing Restreamer volumes. Set
   `BROWSER_RTMP_URL` to the copied URL, replacing its hostname with the
   Restreamer service name in the stack.
3. Redeploy the stack. On your computer, open an SSH tunnel to the server:

   `ssh -L 6080:127.0.0.1:6080 USER@SERVER`

4. Open `http://127.0.0.1:6080/vnc.html` locally. Sign in to the website and
   control playback there. The browser profile persists across restarts.

The control port is bound to server localhost in the example stack. Keep it
private: anyone who can reach it can operate the website and see its account.
The stream is a shared browser session; all viewers see the admin's choices.
Do not put website credentials in environment variables or image files.

The first image is an experiment. Verify website playback, audio, and browser
profile persistence on your server before replacing an existing OBS source.
Browser capture and H.264 encoding use server CPU and memory; this is not a
DRM bypass, and the site must permit your intended use and rebroadcast.

## Separate test stack

`portainer-test-stack.yaml` runs the custom Restreamer image and browser source
together with new, dedicated named volumes. It leaves an existing standalone
Restreamer installation untouched. The test Restreamer UI is available on the
LAN at `http://192.168.1.11:18080/`. A small Caddy proxy protects browser
control with HTTPS and HTTP Basic authentication on
`https://192.168.1.11:16080/vnc.html`. The proxy username is `admin`; set a
unique, strong `CONTROL_PASSWORD` in Portainer's stack environment before
deploying. The password is never stored in this repository. Caddy uses a local
certificate authority, so your browser will initially warn about its
certificate. Verify the address before accepting the warning. Do not forward
port 16080 from your router to the internet.

The browser-source container also publishes its control page on server
loopback only, at `127.0.0.1:16081`. This is for local diagnostics or an SSH
tunnel; it is not reachable from the LAN. Use the protected Caddy URL on port
16080 for LAN access. RTMP remains internal to the test stack. Create a
Restreamer RTMP-server channel using the detected `/live/browser.stream`
input after initial setup.
Keep this test stack private while signing in to the website: the browser's
screen is captured, including any visible login form.

Set `BROWSER_WIDTH`, `BROWSER_HEIGHT`, and `BROWSER_FPS` in Portainer's stack
environment to change the captured display. For 1080p, use `1920`, `1080`,
and initially `24` fps on a host with sufficient CPU. The test stack allows
the browser source to use up to 7 CPU cores; this is a ceiling, not a
reservation. Redeploying the stack applies the new size without deleting the
browser profile. The publisher waits 15 seconds after Chromium starts before
opening the RTMP stream. Set `BROWSER_WARMUP_SECONDS` to adjust this delay if
needed. Software H.264 encoding at 1080p may overload a small server; lower
`BROWSER_FPS` if playback stutters.
