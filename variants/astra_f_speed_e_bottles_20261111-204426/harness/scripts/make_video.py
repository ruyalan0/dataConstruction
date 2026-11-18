"""Assemble immutable per-step RGB frames into a reviewable video."""
import argparse
from pathlib import Path
import subprocess
p=argparse.ArgumentParser();p.add_argument('episode',type=Path);p.add_argument('--ffmpeg',default='ffmpeg');a=p.parse_args()
frames=sorted((a.episode/'frames').glob('step-*-cam_head.jpg'))
if not frames:raise ValueError('No frames')
listing=a.episode/'video-frames.txt'
listing.write_text(''.join("file '"+str(f.resolve())+"'"+chr(10)+'duration 0.1'+chr(10) for f in frames))
subprocess.run([a.ffmpeg,'-hide_banner','-loglevel','error','-f','concat','-safe','0','-i',str(listing),'-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',str(a.episode/'rollout.mp4')],check=True)
