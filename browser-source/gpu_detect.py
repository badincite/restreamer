"""Resolve only the explicitly selected NVIDIA GPU; never select a Plex GPU."""
import os
import re
import subprocess

def pci_bus_id(value):
    match = re.fullmatch(r'([0-9a-fA-F]{4,8}):([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\.([0-7])', value.strip())
    if not match:
        raise ValueError('Unrecognized NVIDIA PCI address')
    domain, bus, device, function = (int(v, 16) for v in match.groups())
    return f'PCI:{bus}{"@" + str(domain) if domain else ""}:{device}:{function}'

def selected_bus(env=None, run=subprocess.run):
    env = os.environ if env is None else env
    override = env.get('NVIDIA_XORG_BUS_ID', 'auto') or 'auto'
    if override != 'auto':
        if not re.fullmatch(r'PCI:\d+(?:@\d+)?:\d+:\d+', override):
            raise ValueError('Invalid NVIDIA_XORG_BUS_ID')
        return override
    gpu = env.get('BROWSER_GPU_UUID') or env.get('NVIDIA_VISIBLE_DEVICES')
    if gpu is None:
        # Some container init systems consume NVIDIA_* runtime variables.
        # Accept only a single visible GPU, never guess among multiple GPUs.
        result = run(['nvidia-smi', '--query-gpu=pci.bus_id', '--format=csv,noheader'],
                     capture_output=True, text=True, timeout=15, check=True)
        lines = result.stdout.strip().splitlines()
        if len(lines) != 1:
            raise ValueError('GPU selector unavailable and more than one GPU is visible')
        return pci_bus_id(lines[0])
    if not re.fullmatch(r'GPU-[a-fA-F0-9-]+', gpu):
        raise ValueError('Select one GPU UUID; automatic all-GPU selection is not allowed')
    result = run(['nvidia-smi', '--id=' + gpu, '--query-gpu=pci.bus_id', '--format=csv,noheader'],
                 capture_output=True, text=True, timeout=15, check=True)
    lines = result.stdout.strip().splitlines()
    if len(lines) != 1:
        raise ValueError('Expected exactly one selected NVIDIA GPU')
    return pci_bus_id(lines[0])

def require_nvenc(run=subprocess.run):
    result = run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                  'color=c=black:s=128x128:r=1', '-frames:v', '1', '-an', '-c:v',
                  'h264_nvenc', '-pix_fmt', 'yuv420p', '-f', 'null', '-'],
                 capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('NVENC H.264 preflight failed. Check GPU support, host driver/FFmpeg compatibility, NVIDIA Container Toolkit and available encoder capacity. ' + result.stderr[-2000:])
