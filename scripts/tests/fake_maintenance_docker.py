#!/usr/bin/env python3
"""A deterministic Docker CLI double; it never touches real containers or volumes."""

import hashlib
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
root = Path(os.environ['FAKE_ROOT'])
project = os.environ.get('FAKE_PROJECT', 'legacy-project')
services = ['open-webui', 'postgres', 'redis', 'minio', 'searxng', 'minio-init']
with Path(os.environ['DOCKER_CALLS']).open('a') as log:
    log.write(json.dumps(args) + '\n')


def pin(ref):
    if '@sha256:' in ref:
        return ref
    repo = 'ghcr.io/albert-li-sz/nefex-web-ui' if ref.startswith('ghcr.io/') else 'registry.test/' + ref
    return repo + '@sha256:' + hashlib.sha256(ref.encode()).hexdigest()


if args[0] == 'compose':
    command = next(x for x in ['version', 'config', 'ps', 'up', 'stop', 'exec'] if x in args)
    if command == 'ps' and '-aq' in args:
        selected = [args[-1]] if args[-1] in services else services
        print('\n'.join('id-' + s for s in selected))
    elif command == 'config' and '--quiet' not in args:
        print('services:\n  open-webui:\n    image: fixture\n')
    elif command == 'up' and os.environ.get('FAIL_NEW_UP') == '1':
        if str(root / 'docker-compose.yaml') in args and (root / 'VERSION').read_text().strip() == '0.12.8':
            sys.exit(42)
    elif command == 'exec' and os.environ.get('FAIL_NEW_CHECK') == '1':
        if (root / 'VERSION').read_text().strip() == '0.12.8':
            sys.exit(43)
    sys.exit(0)
if args[0] == 'info':
    sys.exit(0)
if args[0] == 'ps':
    if any('com.docker.compose.service=' in x for x in args):
        print('id-open-webui')
    elif 'volume=anonymous-searxng' in args and os.environ.get('ANONYMOUS_SHARED'):
        print('id-foreign-consumer')
    elif '-q' in args and any(a.startswith('volume=') for a in args) and os.environ.get('RUNNING_CONSUMER'):
        print('id-active-consumer')
    sys.exit(0)
if args[0] == 'inspect':
    template = args[args.index('--format') + 1]
    service = args[-1].removeprefix('id-')
    if 'working_dir' in template:
        print(os.environ.get('FAKE_WORKING_DIR', str(root)))
    elif 'compose.project' in template:
        print('another-project' if service == 'foreign-consumer' else project)
    elif 'compose.service' in template:
        print(service)
    elif '.DeviceRequests' in template:
        print('true' if os.environ.get('OLD_GPU') else 'false')
    elif '.Image' in template:
        print('old-' + service + '-image')
    elif '.State.Running' in template:
        print('false' if service == 'minio-init' else 'true')
    elif '.Mounts' in template and service != 'minio-init':
        print(project + '_' + service + '\t/data\ttrue')
        if service == 'searxng' and os.environ.get('ANONYMOUS_VOLUME'):
            print('anonymous-searxng\t/etc/searxng\ttrue')
        print()
    sys.exit(0)
if args[:2] == ['image', 'inspect']:
    if '--format' in args:
        print(pin(args[-1]))
    sys.exit(0)
if args[:2] == ['volume', 'inspect']:
    template = args[args.index('--format') + 1] if '--format' in args else ''
    if 'compose.project' in template:
        print(
            '<no value>'
            if args[-1] == 'anonymous-searxng'
            else 'external-project'
            if os.environ.get('EXTERNAL_VOLUME') and args[-1].endswith('_redis')
            else project
        )
    elif 'compose.volume' in template:
        print('<no value>' if args[-1] == 'anonymous-searxng' else args[-1].removeprefix(project + '_'))
    elif 'json .Labels' in template:
        print('{"com.docker.volume.anonymous":""}' if args[-1] == 'anonymous-searxng' else '{}')
    sys.exit(0)
if args[0] == 'pull':
    if os.environ.get('PULL_FAIL'):
        sys.exit(9)
    sys.exit(0)
if args[0] == 'run':
    entrypoint = args[args.index('--entrypoint') + 1]
    if entrypoint == 'python':
        print('0.12.8' if any(x.startswith('ghcr.io/') for x in args) else os.environ.get('OLD_VERSION', '0.12.5'))
    elif entrypoint == 'du':
        print('1\t/data')
    elif entrypoint == 'tar':
        mount = next(x for x in args if x.startswith('type=bind,source='))
        directory = mount.split('source=', 1)[1].split(',target=', 1)[0]
        name = args[args.index('-czpf') + 1].removeprefix('/backup/')
        (Path(directory) / name).write_bytes(b'fixture-volume-archive\n')
    sys.exit(0)
raise SystemExit('Unhandled fake Docker invocation: ' + repr(args))
