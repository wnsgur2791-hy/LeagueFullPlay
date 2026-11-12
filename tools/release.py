# -*- coding: utf-8 -*-
"""새 버전 배포 (GitHub '릴리스' 기능 없이, 저장소 파일만으로)

사용:  python tools/release.py 1.1.1 "바뀐 내용 한 줄" ["두 번째 줄" ...]

하는 일
1. LeagueFullPlay/app/version.py 의 VERSION 을 바꾸고 커밋 + push (main)
2. 그 커밋 번호(ref)와 파일 목록(경로 -> sha256)을 latest.json 에 적고 커밋 + push
   사용자 PC의 app/updater.py 는 켤 때 latest.json 을 읽고, 자기 파일과 다른 것만
   raw.githubusercontent.com/<저장소>/<ref>/LeagueFullPlay/<경로> 에서 받아서 바꿈.
처음 받는 사람은 README의 '전체 받기 (zip)' 링크로 전체를 받음.
"""
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROG_NAME = 'LeagueFullPlay'
PROG = os.path.join(HERE, PROG_NAME)
ROOT_FILES = ('사용법.txt', 'requirements.txt')
ATTR = ('\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n'
        'Claude-Session: https://claude.ai/code/session_016Z9N2Zzj4nYb4jGjn7DzeD')


def git(*a):
    out = subprocess.run(['git', '-C', HERE] + list(a), capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f'git {" ".join(a)} 실패:\n{out.stderr or out.stdout}')
    return out.stdout.strip()


def commit_push(msg):
    git('add', '-A')
    if git('status', '--porcelain'):
        git('-c', 'user.name=wnsgur2791-hy', '-c', 'user.email=wnsgur2791-hy@users.noreply.github.com',
            'commit', '-q', '-m', msg + ATTR)
    git('push', 'origin', 'HEAD:main')
    return git('rev-parse', 'HEAD')


def manifest():
    files = {}
    app = os.path.join(PROG, 'app')
    for root, dirs, fs in os.walk(app):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for f in fs:
            if f.endswith(('.pyc', '.tmp')):
                continue
            p = os.path.join(root, f)
            files[os.path.relpath(p, PROG).replace(os.sep, '/')] = hashlib.sha256(open(p, 'rb').read()).hexdigest()
    for f in ROOT_FILES:
        p = os.path.join(PROG, f)
        if os.path.isfile(p):
            files[f] = hashlib.sha256(open(p, 'rb').read()).hexdigest()
    return dict(sorted(files.items()))


DIST_ZIP = os.path.join(HERE, 'dist', '컴프야매크로.zip')
TOP = '컴프야 매크로'                       # zip 을 풀면 나오는 폴더 이름
LAUNCHER = '컴프야 매크로.bat'
USER_ONLY = {'config.json', '.installed'}   # 사용자 PC에서만 생기는 것 (넣지 않음)
USER_DIRS = {'__pycache__', 'logs', 'captures', '업데이트백업'}


def build_dist():
    """배포용 zip: 컴프야 매크로/컴프야 매크로.bat + 컴프야 매크로/LeagueFullPlay/..."""
    import zipfile
    os.makedirs(os.path.dirname(DIST_ZIP), exist_ok=True)
    n = 0
    with zipfile.ZipFile(DIST_ZIP, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(HERE, LAUNCHER), f'{TOP}/{LAUNCHER}')
        for root, dirs, fs in os.walk(PROG):
            dirs[:] = sorted(d for d in dirs if d not in USER_DIRS)
            for f in sorted(fs):
                rel = os.path.relpath(os.path.join(root, f), PROG).replace(os.sep, '/')
                if rel in USER_ONLY or f.endswith(('.pyc', '.tmp')):
                    continue
                z.write(os.path.join(root, f), f'{TOP}/{PROG_NAME}/{rel}')
                n += 1
    return n


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    v, notes = sys.argv[1].lstrip('vV'), sys.argv[2:]
    p = os.path.join(PROG, 'app', 'version.py')
    s = open(p, encoding='utf-8').read()
    open(p, 'w', encoding='utf-8').write(re.sub(r'VERSION = "[^"]*"', f'VERSION = "{v}"', s))
    ref = commit_push(f'리그 풀플레이 v{v}\n\n' + '\n'.join(f'- {n}' for n in notes))
    latest = {'version': v, 'ref': ref, 'prog_dir': PROG_NAME, 'notes': notes, 'files': manifest()}
    with open(os.path.join(HERE, 'latest.json'), 'w', encoding='utf-8') as f:
        json.dump(latest, f, ensure_ascii=False, indent=1)
    nz = build_dist()
    commit_push(f'latest.json + 배포용 zip -> v{v}')
    print(f'배포용 zip: dist/컴프야매크로.zip (파일 {nz + 1}개)')
    print(f'v{v} 배포 완료 (ref {ref[:8]}, 파일 {len(latest["files"])}개)')
