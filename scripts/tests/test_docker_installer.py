"""Installer regression tests; Docker calls are intercepted, no volumes are touched."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
SECRETS = ('POSTGRES_PASSWORD', 'REDIS_PASSWORD', 'MINIO_ROOT_PASSWORD', 'WEBUI_SECRET_KEY', 'SEARXNG_SECRET')


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'scripts').mkdir()
        shutil.copy(REPO / 'scripts/install-docker.sh', self.root / 'scripts/install-docker.sh')
        shutil.copy(REPO / '.env.example', self.root / '.env.example')
        self.calls = self.root / 'calls'
        fake_docker = self.root / 'docker'
        fake_docker.write_text(
            '#!/bin/sh\n'
            'printf "%s\\n" "$*" >> "$DOCKER_CALLS"\n'
            'case "$*" in *"up -d"*) exit "${UP_EXIT:-0}";; esac\n'
            'exit 0\n'
        )
        fake_docker.chmod(0o755)
        self.environment = {**os.environ, 'PATH': f'{self.root}:{os.environ["PATH"]}', 'DOCKER_CALLS': str(self.calls)}

    def run_installer(self, *arguments, **environment):
        return subprocess.run(
            ['bash', str(self.root / 'scripts/install-docker.sh'), *arguments],
            cwd=self.root,
            env={**self.environment, **environment},
            capture_output=True,
            text=True,
        )

    def test_generates_distinct_private_secrets_and_is_idempotent(self):
        self.assertEqual(self.run_installer('--init-only').returncode, 0)
        env_file = self.root / '.env'
        first = env_file.read_bytes()
        values = [re.search(rf'^{key}=([0-9a-f]{{64}})$', first.decode(), re.M).group(1) for key in SECRETS]
        self.assertEqual(len(set(values)), len(SECRETS))
        self.assertEqual(env_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.run_installer('--init-only').returncode, 0)
        self.assertEqual(env_file.read_bytes(), first)
        self.assertNotIn(' up ', self.calls.read_text())

    def test_keeps_existing_config_and_does_not_execute_it(self):
        env_file = self.root / '.env'
        env_file.write_text('POSTGRES_PASSWORD=existing-password\nOPENAI_API_KEY=$(touch injected)\n')
        result = self.run_installer('--init-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('POSTGRES_PASSWORD=existing-password', env_file.read_text())
        self.assertIn('OPENAI_API_KEY=$(touch injected)', env_file.read_text())
        self.assertFalse((self.root / 'injected').exists())

    def test_custom_env_path_with_spaces(self):
        env_file = self.root / 'custom settings.env'
        result = self.run_installer('--env-file', str(env_file), '--init-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(env_file.is_file())
        self.assertFalse((self.root / '.env').exists())

    def test_relative_env_path_is_resolved_from_the_invoking_directory(self):
        result = self.run_installer('--env-file', 'relative.env', '--init-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'--env-file {self.root.resolve()}/relative.env', self.calls.read_text())

    def test_failed_start_does_not_report_success_or_run_checks(self):
        result = self.run_installer(UP_EXIT='42')
        self.assertNotEqual(result.returncode, 0)
        calls = self.calls.read_text()
        self.assertIn(' pull', calls)
        self.assertIn('logs --tail 60', calls)
        self.assertNotIn('python check_stack.py', calls)
        self.assertNotIn('Installation checks passed', result.stdout)

    def test_default_uses_published_images_and_build_is_explicit(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls.read_text()
        self.assertIn(' pull', calls)
        self.assertNotIn(' build', calls)
        self.calls.unlink()
        result = self.run_installer('--build')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls.read_text()
        self.assertIn('build minio', calls)
        self.assertIn('build open-webui', calls)
        self.calls.unlink()
        result = self.run_installer('--no-build')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls.read_text()
        self.assertNotIn(' pull', calls)
        self.assertNotIn(' build', calls)

    def test_check_mode_never_builds_or_restarts_services(self):
        result = self.run_installer('--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls.read_text()
        self.assertIn('exec -T open-webui python check_stack.py', calls)
        self.assertNotIn(' up ', calls)
        self.assertNotIn(' build', calls)


if __name__ == '__main__':
    unittest.main()
