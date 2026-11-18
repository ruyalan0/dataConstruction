"""Export completed local episodes to MP4; optionally watch an existing campaign."""
import argparse
from datetime import datetime, timezone
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

CAMERAS = ('cam_head', 'cam_left_wrist', 'cam_right_wrist')


def read(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save(path, value):
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    temporary.replace(path)


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=codec_name,pix_fmt,width,height,r_frame_rate,nb_frames:format=duration,size',
        '-of', 'json', str(path)], capture_output=True, text=True, check=True, timeout=30)
    data = json.loads(result.stdout)
    stream = data['streams'][0]
    stream.update(duration=float(data['format']['duration']), size=int(data['format']['size']))
    if stream['duration'] <= 0 or stream['size'] <= 0:
        raise ValueError('Empty video: '+str(path))
    return stream


def recorded_inputs(episode):
    """Only combine streams with matching frame counts and cadence."""
    candidates = list((episode/'eval_result').rglob('*.mp4'))
    selected, details = {}, {}
    for camera in CAMERAS:
        matches = [path for path in candidates if re.search(r'_'+camera+r'(?:[_.])', path.name)]
        if len(matches) != 1:
            raise ValueError('Missing or ambiguous camera recording: '+camera)
        selected[camera] = matches[0]
        details[camera] = probe(matches[0])
    signatures = {(int(info['nb_frames']), Fraction(info['r_frame_rate'])) for info in details.values()}
    if len(signatures) != 1 or next(iter(signatures))[0] < 1:
        raise ValueError('Camera recordings are not synchronized')
    return selected, details


def image_inputs(episode, workspace):
    """Preserve numeric step order, including the initial frame, without copying JPGs."""
    frames = episode/'frames'
    entries = sorted(((int(match.group(1)), path.name.removesuffix('-cam_head.jpg'))
                      for path in frames.glob('step-*-cam_head.jpg')
                      if (match := re.fullmatch(r'step-(\d+)-cam_head\.jpg', path.name))),
                     key=lambda item: item[0])
    if (frames/'initial-cam_head.jpg').is_file():
        entries.insert(0, (0, 'initial'))
    if not entries:
        return {}, {}
    cameras = [camera for camera in CAMERAS
               if all((frames/f'{tag}-{camera}.jpg').is_file() for _, tag in entries)]
    if 'cam_head' not in cameras:
        return {}, {}
    if len(cameras) != 3:
        cameras = ['cam_head']
    selected = {}
    for camera in cameras:
        folder = workspace/camera
        folder.mkdir()
        for number, (_, tag) in enumerate(entries):
            (folder/f'{number:08d}.jpg').symlink_to((frames/f'{tag}-{camera}.jpg').resolve())
        selected[camera] = folder/'%08d.jpg'
    return selected, {'frame_count': len(entries), 'fps': 10,
                      'first_step': entries[0][0], 'last_step': entries[-1][0]}


def encode(inputs, destination, log, *, images=False):
    command = ['ffmpeg', '-nostdin', '-n', '-hide_banner', '-loglevel', 'error', '-xerror']
    for source in inputs.values():
        command.extend(['-threads', '2'])
        if images:
            command.extend(['-framerate', '10', '-start_number', '0'])
        command.extend(['-i', str(source)])
    head = '[0:v]setpts=PTS-STARTPTS,scale=640:480:force_original_aspect_ratio=decrease,' \
           'pad=640:480:(ow-iw)/2:(oh-ih)/2,setsar=1,' \
           "drawtext=text='HEAD':fontsize=22:fontcolor=white:box=1:boxcolor=black@0.6:x=8:y=8[h]"
    if len(inputs) == 3:
        filters = [head]
        for index, (label, tag) in enumerate((('LEFT WRIST', 'l'), ('RIGHT WRIST', 'r')), 1):
            filters.append(f'[{index}:v]setpts=PTS-STARTPTS,scale=320:240:force_original_aspect_ratio=decrease,'
                'pad=320:240:(ow-iw)/2:(oh-ih)/2,setsar=1,'
                f"drawtext=text='{label}':fontsize=18:fontcolor=white:box=1:boxcolor=black@0.6:x=8:y=8[{tag}]")
        filters.extend(['[l][r]vstack=inputs=2[w]', '[h][w]hstack=inputs=2[out]'])
    else:
        filters = [head, '[h]null[out]']
    command.extend(['-filter_complex_threads', '1', '-filter_complex', ';'.join(filters),
        '-map', '[out]', '-c:v', 'libx264', '-threads', '4', '-preset', 'fast', '-crf', '20',
        '-pix_fmt', 'yuv420p', '-an', '-movflags', '+faststart', str(destination)])
    with log.open('a') as stream:
        subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=900)
    return probe(destination)


def export_run(run):
    """Rendering failures are recorded separately and never alter episode results."""
    run = Path(run).resolve()
    results = read(run/'results.json')
    if not results:
        return {'status': 'pending', 'run': str(run)}
    folder = run/'rendered'
    folder.mkdir(exist_ok=True)
    with (folder/'.export.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'busy', 'run': str(run)}
        records = []
        for result in results:
            seed = result['seed_index']
            video = folder/('rollout.mp4' if len(results) == 1 else f'seed-{seed}.mp4')
            row = {'seed': seed, 'task': result.get('task'), 'task_success': result.get('success'),
                   'task_status': result.get('status'), 'video': str(video)}
            old = read(folder/'video-export.json', {})
            previous = next((item for item in old.get('episodes', []) if item['seed'] == seed), None)
            try:
                if video.exists():
                    info = probe(video)
                    row = previous if previous and previous.get('status') in ('completed', 'existing') else dict(row, status='existing', media=info)
                else:
                    episode = run/f'seed-{seed}'
                    with tempfile.TemporaryDirectory(prefix='encoding-', dir=folder) as temporary:
                        workspace = Path(temporary)
                        pending = workspace/'recorded.mp4'
                        try:
                            inputs, source_info = recorded_inputs(episode)
                            info = encode(inputs, pending, folder/f'ffmpeg-{seed}.log')
                            row.update(source='recorded_video', source_files={key: str(path) for key, path in inputs.items()},
                                       source_media=source_info)
                        except (OSError, ValueError, KeyError, IndexError, ZeroDivisionError, subprocess.SubprocessError) as error:
                            row['recording_fallback_reason'] = str(error)
                            inputs, source_info = image_inputs(episode, workspace)
                            if not inputs:
                                row.update(status='no_frames', video=None)
                                records.append(row)
                                continue
                            pending = workspace/'frames.mp4'
                            info = encode(inputs, pending, folder/f'ffmpeg-{seed}.log', images=True)
                            row.update(source='per_step_images', source_media=source_info)
                        if video.exists():
                            raise FileExistsError('Preserving existing video: '+str(video))
                        pending.replace(video)
                        row.update(status='completed', media=info, cameras=list(inputs),
                                   layout='head_left_wrists_right' if len(inputs) == 3 else 'head_only')
            except Exception as error:
                row.update(status='error', error=f'{type(error).__name__}: {error}')
            records.append(row)
        report = {'status': 'error' if any(row['status'] == 'error' for row in records) else 'finished',
                  'updated_at': datetime.now(timezone.utc).isoformat(),
                  'exporter_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'scope': 'Recorded playback; model waiting time omitted; no simulator or API calls.',
                  'episodes': records}
        save(folder/'video-export.json', report)
        return report


def finalize_run(run):
    """Best-effort hook: preserve the harness exit code even if the export fails."""
    try:
        report = export_run(run)
        for row in report.get('episodes', []):
            print(f'[video] seed {row["seed"]}: {row["status"]} {row.get("video") or "(no frames)"}', flush=True)
        return report
    except Exception as error:
        print(f'[video] export failed: {type(error).__name__}: {error}', flush=True)
        return {'status': 'error', 'error': str(error)}


def alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def campaign_pass(campaign):
    plan = read(campaign/'plan.json')
    if not plan:
        raise ValueError('Campaign plan is missing')
    rows = []
    for phase in plan['phases']:
        for job in phase:
            run = campaign/f'{job["task"]}-L{job["seed"]}'
            if not read(run/'results.json'):
                continue
            report = read(run/'rendered/video-export.json')
            if not report or report.get('status') not in ('finished', 'error'):
                report = finalize_run(run)
            rows.append(dict(job, report=report))
    return rows


def watch_campaign(campaign, watch):
    with (campaign/'.video-watch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            rows = campaign_pass(campaign)
            active = read(campaign/'active.json', {})
            scheduler = read(campaign/'plan.json', {}).get('scheduler_pid')
            running = alive(scheduler) or any(alive(job.get('pid')) for job in active.get('jobs', []))
            finished = active.get('status') == 'finished' or not running
            save(campaign/'video-export-status.json', {
                'status': 'finished' if finished else 'watching', 'pid': os.getpid(),
                'updated_at': datetime.now(timezone.utc).isoformat(), 'runs': rows})
            if not watch or finished:
                return
            time.sleep(15)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path, help='Run directory, or campaign directory with --campaign')
    parser.add_argument('--campaign', action='store_true')
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--detach', action='store_true')
    args = parser.parse_args()
    path = args.path.resolve()
    if args.watch and not args.campaign:
        parser.error('--watch requires --campaign')
    if args.detach:
        if not (args.campaign and args.watch):
            parser.error('--detach requires --campaign --watch')
        command = [sys.executable, '-u', '-B', str(Path(__file__).resolve()), str(path), '--campaign', '--watch']
        with (path/'video-export.log').open('a') as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
        print(json.dumps({'pid': process.pid, 'campaign': str(path), 'log': str(path/'video-export.log')}))
    elif args.campaign:
        watch_campaign(path, args.watch)
    else:
        print(json.dumps(finalize_run(path), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
