# NVIDIA browser desktops for Restreamer

Channel-owned Chrome desktops, embedded admin controls, NVIDIA-backed Xorg
rendering and H.264 NVENC screen/audio publishing. This captures the desktop;
it does not require extracting the website's media URL.

## Compatibility, not a promise of every NVIDIA GPU

Linux amd64, a working NVIDIA host driver and NVIDIA Container Toolkit configured
for Docker's `nvidia` runtime are required. Select one GPU UUID explicitly.
The worker discovers its PCI BusID using `nvidia-smi`; PCI decimal/domain
conversion is automatic. Optional `GPU_BUS_ID`, `GPU_RENDER_NODE` and
`GPU_CARD_NODE` override host-specific mappings. No model, UUID or host IP is
hardcoded in the public template.

The GPU must support NVIDIA Xorg/OpenGL and NVENC H.264 with the installed host
driver and container FFmpeg. Startup performs an actual one-frame encoder test
and fails clearly if this combination is unsupported or encoder capacity is
exhausted. Not all NVIDIA products have NVENC or usable display support.
CPU video decoding remains the default; GPU rendering and encoding are separate.
Chrome hardware decoding is not promised across GPU generations.

Both the previous overlay and a fresh build of this portable worker were
validated on a Quadro K420 with a legacy driver: automatic PCI discovery,
NVENC H.264 preflight, a healthy blank browser and an actual NVIDIA GLX renderer.
There is not a tested matrix of every GPU model or a multi-stream capacity
guarantee for other cards.
The Debian FFmpeg baseline retains older NVENC API compatibility; newer drivers
and very new GPU architectures still need independent verification. Do not
blindly upgrade FFmpeg/NVENC headers on a legacy driver.

NVIDIA references: [Container Toolkit GPU selection/capabilities](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html),
[NVENC compatibility and encoder capacity](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/nvenc-application-note/index.html).

## Build from source

These feature sources are in `badincite/restreamer` and `badincite/restreamer-ui`,
both on `feature/nvidia-browser-desktops`. Clone the bundle and clone that UI
branch into its `ui/` folder (the normal bundle Dockerfile expects this layout).
From the bundle root:

```sh
docker build -f Dockerfile.badincite --build-arg NODE_IMAGE=node:22-bookworm-slim -t badincite/restreamer:browser-ui .
docker build -t badincite/restreamer:browser-manager browser-manager
docker build -f browser-manager/Dockerfile.gateway -t badincite/restreamer:browser-gateway browser-manager
docker build -f browser-source/Dockerfile.nvidia -t badincite/restreamer:browser-nvidia browser-source
```

Only public upstream base images are required; private/local experimental
image tags are not build dependencies. GitHub Actions builds the components
and runs tests. It does not publish images or require Docker Hub credentials.
Build locally before importing the stack into Portainer. Do not assume the
example image tags have already been uploaded to a registry.

## Configure and deploy

Copy `.env.example` to `.env` and set strong admin/JWT secrets, `GPU_UUID`,
`PUBLIC_HOST` and the matching `PUBLIC_ORIGIN` including its HTTPS port.
List GPUs with `nvidia-smi --query-gpu=uuid,name,pci.bus_id --format=csv`.
Optional DRM nodes must correspond to that same GPU, not another application's.
Keep `COMPOSE_PROJECT_NAME` consistent with the actual Portainer stack name;
the dynamic workers join that stack's `_default` network.

```sh
docker compose --env-file browser-manager/.env -f browser-manager/compose.nvidia.yaml up -d
```

The gateway publishes only HTTPS port 18443 by default. Admin UI is `/ui/`;
the root is the public playersite. Caddy uses a local CA: trust its certificate
or accept the home-lab warning. Workers have no individually published ports.
Only admin-authenticated requests can manage sessions or obtain browser controls.
`DEFAULT_WEBSITE` is `about:blank`; your private runtime URL never needs to be
committed. Set it in Portainer if desired.

## No two-session application limit

`MAX_WORKERS=0` means no application concurrency cap. A positive value provides
an optional administrator safety cap and can be any positive count, not just two.
This does **not** bypass NVIDIA driver restrictions, add encoder hardware,
or guarantee unlimited smooth streams. GPU memory, rendering/encode throughput,
CPU decoding and host/VM scheduling remain limiting factors. No driver patch is
bundled. `MAX_SESSIONS` is a separate saved-record storage limit, default 1000.

Worker CPU, RAM and shared-memory limits are configurable with `WORKER_CPUS`,
`WORKER_MEMORY_MB` and `WORKER_SHM_MB`; CPU 0 means no Docker CPU quota.
Limits are per browser, so size the host before creating many sessions.
Stop/restart a worker to apply changed template/resource settings.

## Channel lifecycle and security

Selecting Browser desktop during channel setup creates its stopped container
automatically; configure it and Start. Deleting the channel or confirming wizard
cancellation stops/removes its worker and saved session. Saved login-profile
volumes are retained for recovery. The session manager supports confirmed
deletion of legacy/orphaned entries and deletes an existing associated channel
with its browser. Closing a tab is not a confirmed deletion. Direct Core API
or manual Docker deletions are not reconciled by a background garbage collector.

The manager mounts Docker's socket: this is host-administrator access, not an
untrusted multi-tenant sandbox. Run on a trusted private host. It accepts only
fixed worker templates, validates ownership labels and does not expose Docker
credentials/socket to the frontend. Keep profiles and `.env` private.
Removing the stack does not remove dynamic workers: stop them first.

Public export excludes local Portainer/SSH/deployment helpers, tokens, profiles,
private runtime website defaults and experimental host-specific stack files.
