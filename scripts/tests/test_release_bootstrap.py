"""Verified-release download and standalone entrypoint tests; no network requests."""

import hashlib
import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = self.root / 'payload'
        self.payload.mkdir()
        self.download = self.root / 'download'
        self.download.mkdir()
        fake = self.root / 'curl'
        fake.write_text("""#!/usr/bin/env python3
import os,sys
from pathlib import Path
args=sys.argv[1:]
if os.environ.get('CURL_FAIL'):sys.exit(22)
if '--head' in args:
 print('https://github.com/Albert-Li-Sz/Nefex-Web-UI/releases/tag/v0.12.7',end='');sys.exit(0)
url=next(x for x in args if x.startswith('https://'))
out=args[args.index('-o')+1]
Path(out).write_bytes((Path(os.environ['PAYLOAD'])/url.rsplit('/',1)[1]).read_bytes())
""")
        fake.chmod(0o755)
        self.env = {
            **os.environ,
            'PATH': str(self.root) + os.pathsep + os.environ['PATH'],
            'PAYLOAD': str(self.payload),
            'FIXTURE_RESULT': str(self.root / 'entrypoint-args'),
        }
        self.make_archive()

    def make_archive(self, special=None, version='0.12.7'):
        prefix = 'nefex-web-ui-v0.12.7-docker/'
        archive = self.payload / 'nefex-web-ui-v0.12.7-docker.tar.gz'
        files = {
            'VERSION': version + '\n',
            'install-files.txt': 'VERSION\ninstall-files.txt\nscripts/install-docker.sh\n',
            'scripts/install-docker.sh': '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$FIXTURE_RESULT"\n',
        }
        with tarfile.open(archive, 'w:gz') as tar:
            for name, content in files.items():
                info = tarfile.TarInfo(prefix + name)
                raw = content.encode()
                info.size = len(raw)
                info.mode = 0o755 if name.endswith('.sh') else 0o644
                tar.addfile(info, io.BytesIO(raw))
            if special == 'traversal':
                info = tarfile.TarInfo(prefix + '../escape')
                info.size = 1
                tar.addfile(info, io.BytesIO(b'x'))
            if special == 'link':
                info = tarfile.TarInfo(prefix + 'link')
                info.type = tarfile.SYMTYPE
                info.linkname = '/tmp'
                tar.addfile(info)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (self.payload / 'SHA256SUMS').write_text(digest + '  ' + archive.name + '\n')

    def fetch(self, version='v0.12.7', **env):
        return subprocess.run(
            [
                'bash',
                '-c',
                'source "$1/install.sh"; nefex_distribution "$2" "$3"',
                '--',
                str(REPO),
                version,
                str(self.download),
            ],
            env={**self.env, **env},
            capture_output=True,
            text=True,
        )

    def test_latest_release_is_downloaded_and_verified(self):
        result = self.fetch('latest')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(Path(result.stdout.strip()).is_dir())

    def test_checksum_mismatch_never_extracts_or_executes_the_package(self):
        sums = self.payload / 'SHA256SUMS'
        sums.write_text('0' * 64 + '  nefex-web-ui-v0.12.7-docker.tar.gz\n')
        result = self.fetch()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.download / 'nefex-web-ui-v0.12.7-docker').exists())
        self.assertFalse((self.root / 'entrypoint-args').exists())

    def test_network_errors_stop_before_extraction(self):
        result = self.fetch(CURL_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.download / 'nefex-web-ui-v0.12.7-docker').exists())

    def test_archive_parent_traversal_is_rejected(self):
        self.make_archive(special='traversal')
        self.assertNotEqual(self.fetch().returncode, 0)
        self.assertFalse((self.download / 'escape').exists())

    def test_archive_links_are_rejected(self):
        self.make_archive(special='link')
        self.assertNotEqual(self.fetch().returncode, 0)
        self.assertFalse((self.download / 'nefex-web-ui-v0.12.7-docker').exists())

    def test_archive_version_must_match_release_tag(self):
        self.make_archive(version='0.12.5')
        self.assertNotEqual(self.fetch().returncode, 0)

    def test_non_version_ref_is_rejected(self):
        self.assertNotEqual(self.fetch('main').returncode, 0)
        self.assertFalse(list(self.download.iterdir()))

    def test_standalone_entrypoint_installs_without_forwarded_arguments(self):
        target = self.root / 'new installation'
        result = subprocess.run(
            ['bash', str(REPO / 'install.sh'), '--install-dir', str(target)],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((target / 'VERSION').exists())
        self.assertEqual((self.root / 'entrypoint-args').read_text(), '\n')

    def test_existing_env_automatically_selects_update_and_forwards_automation(self):
        target = self.root / 'existing installation'
        target.mkdir()
        (target / '.env').write_text('KEEP=original\n')
        result = subprocess.run(
            ['bash', str(REPO / 'install.sh'), '--install-dir', str(target), '--non-interactive'],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.root / 'entrypoint-args').read_text().splitlines()
        self.assertEqual(args, ['--update', '--local', '--install-dir', str(target), '--non-interactive'])
        self.assertEqual((target / '.env').read_text(), 'KEEP=original\n')

    def test_unrelated_nonempty_directory_is_not_overwritten(self):
        target = self.root / 'unrelated'
        target.mkdir()
        (target / 'keep.txt').write_text('keep')
        result = subprocess.run(
            ['bash', str(REPO / 'install.sh'), '--install-dir', str(target)],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse((target / 'VERSION').exists())
        self.assertEqual((target / 'keep.txt').read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
