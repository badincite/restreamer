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
