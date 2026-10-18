"""Maintenance transaction regressions. All Docker operations use a local CLI double."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nefex maintenance ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'old installation'
        self.root.mkdir()
        for name in ['.env.example', 'docker-compose.yaml', 'docker-compose.gpu.yaml', 'deploy/searxng/settings.yml']:
            dest = self.root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / name, dest)
        (self.root / 'VERSION').write_text('0.12.5\n')
        (self.root / 'README.md').write_text('Original deployment notes\n')
        self.original = '\n'.join(
            [
                'POSTGRES_PASSWORD=postgres-fixture-secret',
                'REDIS_PASSWORD=redis-fixture-secret',
                'MINIO_ROOT_PASSWORD=minio-fixture-secret',
                'WEBUI_SECRET_KEY=session-fixture-secret',
                'SEARXNG_SECRET=search-fixture-secret',
                'WEBUI_IMAGE=ghcr.io/albert-li-sz/nefex-web-ui:v0.12.5',
                'OPENAI_API_KEY=$(touch injected)',
                '# Unknown settings are preserved',
                'CUSTOM_SETTING=keep-me',
                '',
            ]
        )
        (self.root / '.env').write_text(self.original)
        (self.root / '.env').chmod(0o600)
        self.calls = self.root / 'docker-calls.jsonl'
        executable = self.root / 'docker'
        shutil.copy(REPO / 'scripts/tests/fake_maintenance_docker.py', executable)
        executable.chmod(0o755)
        self.env = {
            **os.environ,
            'PATH': str(self.root) + os.pathsep + os.environ['PATH'],
            'DOCKER_CALLS': str(self.calls),
            'FAKE_ROOT': str(self.root),
            'FAKE_PROJECT': 'legacy-project',
        }

    def run_maintenance(self, *args, input=None, **env):
        return subprocess.run(
            ['bash', str(REPO / 'scripts/install-docker.sh'), '--install-dir', str(self.root), *args],
            cwd=self.root,
            env={**self.env, **env},
            text=True,
            input=input,
            capture_output=True,
            timeout=30,
        )

    def events(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def snapshots(self):
        return sorted((self.root / 'backups').glob('*/complete'))

    def test_update_preserves_legacy_project_secrets_and_unknown_settings(self):
        result = self.run_maintenance('--update', '--local', '--non-interactive')
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (self.root / '.env').read_text()
        for original in self.original.splitlines():
            if not original.startswith('WEBUI_IMAGE='):
                self.assertIn(original, config)
        self.assertIn('COMPOSE_PROJECT_NAME=legacy-project', config)
        self.assertIn('POSTGRES_IMAGE=registry.test/old-postgres-image@sha256:', config)
        self.assertIn('WEBUI_IMAGE=ghcr.io/albert-li-sz/nefex-web-ui@sha256:', config)
        self.assertEqual((self.root / 'VERSION').read_text().strip(), '0.12.9')
        self.assertFalse((self.root / 'injected').exists())
        self.assertNotIn('postgres-fixture-secret', result.stdout + result.stderr)
        self.assertEqual(len(self.snapshots()), 1)
        self.assertEqual((self.snapshots()[0].parent / 'environment.env').read_text(), self.original)
        self.assertEqual(self.snapshots()[0].parent.stat().st_mode & 0o777, 0o700)

    def test_v0124_layout_uses_the_same_preserving_update_path(self):
        (self.root / 'VERSION').write_text('0.12.4\n')
        result = self.run_maintenance('--update', '--local', '--non-interactive', OLD_VERSION='0.12.4')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('v0.12.4 → v0.12.9', result.stdout)
        self.assertIn('session-fixture-secret', (self.root / '.env').read_text())

    def test_update_detects_existing_cuda_mode(self):
        result = self.run_maintenance('--update', '--local', '--non-interactive', OLD_GPU='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('NEFEX_INSTALL_GPU=true', (self.root / '.env').read_text())
        self.assertTrue(any(e[0] == 'pull' and e[-1].endswith('-cuda') for e in self.events()))

    def test_cpu_override_keeps_old_cuda_config_in_recovery_snapshot(self):
        result = self.run_maintenance('--update', '--local', '--cpu', '--non-interactive', OLD_GPU='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('NEFEX_INSTALL_GPU=false', (self.root / '.env').read_text())
        snapshot_configs = [e for e in self.events() if 'config' in e and e[-1] == 'config']
        self.assertTrue(any(str(self.root / 'docker-compose.gpu.yaml') in e for e in snapshot_configs))
        new_ups = [e for e in self.events() if 'up' in e and str(self.root / 'docker-compose.yaml') in e]
        self.assertTrue(new_ups)
        self.assertFalse(any(str(self.root / 'docker-compose.gpu.yaml') in e for e in new_ups))

    def test_failed_image_pull_does_not_stop_services_or_overwrite_config(self):
        result = self.run_maintenance('--update', '--local', '--non-interactive', PULL_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / '.env').read_text(), self.original)
        self.assertFalse(any('stop' in e for e in self.events()))
        self.assertFalse(self.snapshots())

    def test_failed_update_automatically_restores_config_data_and_old_images(self):
        result = self.run_maintenance('--update', '--local', '--non-interactive', FAIL_NEW_UP='1')
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual((self.root / '.env').read_text(), self.original)
        self.assertEqual((self.root / 'VERSION').read_text().strip(), '0.12.5')
        self.assertFalse((self.root / 'scripts/update-docker.sh').exists())
        self.assertIn('已恢复原配置、数据和镜像', result.stderr)
        self.assertTrue(any('find /data' in ' '.join(e) for e in self.events()))
        self.assertTrue(
            any('up' in e and any('/backups/' in a and a.endswith('/compose.yaml') for a in e) for e in self.events())
        )

    def test_failed_post_update_checks_also_restore_the_snapshot(self):
        result = self.run_maintenance('--update', '--local', '--non-interactive', FAIL_NEW_CHECK='1')
        self.assertEqual(result.returncode, 43, result.stderr)
        self.assertEqual((self.root / '.env').read_text(), self.original)
        self.assertEqual((self.root / 'VERSION').read_text().strip(), '0.12.5')

    def test_backup_keeps_configuration_and_resumes_the_old_stack(self):
        result = self.run_maintenance('--backup', '--non-interactive')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / '.env').read_text(), self.original)
        self.assertEqual(len(self.snapshots()), 1)
        self.assertFalse(any(e[0] == 'pull' for e in self.events()))
        self.assertTrue(any('up' in e for e in self.events()))

    def test_caller_directory_archives_do_not_change_the_snapshot_checksum_list(self):
        (self.root / 'volume-1.tar.gz').write_bytes(b'unrelated caller file')
        result = self.run_maintenance('--backup', '--non-interactive')
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = self.snapshots()[0].parent
        sums = (snapshot / 'SHA256SUMS').read_text()
        for number in range(1, 6):
            self.assertIn('volume-' + str(number) + '.tar.gz', sums)

    def test_volume_consumers_remaining_after_stop_abort_backup_and_resume_services(self):
        result = self.run_maintenance('--backup', '--non-interactive', RUNNING_CONSUMER='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.snapshots())
        self.assertTrue(any('stop' in e for e in self.events()))
        self.assertTrue(any('up' in e for e in self.events()))
        self.assertFalse(any(e[0] == 'run' and 'tar' in e for e in self.events()))

    def test_rollback_checks_hashes_before_stopping_or_modifying_data(self):
        self.assertEqual(self.run_maintenance('--backup', '--non-interactive').returncode, 0)
        snapshot = self.snapshots()[0].parent
        (snapshot / 'environment.env').write_text('corrupted')
        self.calls.unlink()
        result = self.run_maintenance('--rollback', str(snapshot), '--non-interactive')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('stop' in e for e in self.events()))
        self.assertEqual((self.root / '.env').read_text(), self.original)

    def test_manual_rollback_first_saves_current_state(self):
        self.assertEqual(self.run_maintenance('--update', '--local', '--non-interactive').returncode, 0)
        snapshot = self.snapshots()[0].parent
        result = self.run_maintenance('--rollback', str(snapshot), '--non-interactive')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.snapshots()), 2)
        self.assertEqual((self.root / '.env').read_text(), self.original)
        self.assertEqual((self.root / 'VERSION').read_text().strip(), '0.12.5')

    def test_empty_checksum_manifest_cannot_bypass_restore_validation(self):
        self.assertEqual(self.run_maintenance('--backup', '--non-interactive').returncode, 0)
        snapshot = self.snapshots()[0].parent
        (snapshot / 'SHA256SUMS').write_text('')
        self.calls.unlink()
        result = self.run_maintenance('--rollback', str(snapshot), '--non-interactive')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('stop' in e for e in self.events()))

    def test_omitted_volume_checksum_is_rejected_before_stopping_services(self):
        self.assertEqual(self.run_maintenance('--backup', '--non-interactive').returncode, 0)
        snapshot = self.snapshots()[0].parent
        sums = snapshot / 'SHA256SUMS'
        sums.write_text(
            '\n'.join(line for line in sums.read_text().splitlines() if not line.endswith('volume-1.tar.gz')) + '\n'
        )
        self.calls.unlink()
        result = self.run_maintenance('--rollback', str(snapshot), '--non-interactive')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('stop' in e for e in self.events()))

    def test_external_volumes_are_rejected_before_the_stack_is_stopped(self):
        result = self.run_maintenance('--backup', '--non-interactive', EXTERNAL_VOLUME='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('stop' in e for e in self.events()))

    def test_image_declared_anonymous_config_volumes_are_backed_up_and_pinned(self):
        result = self.run_maintenance('--backup', '--non-interactive', ANONYMOUS_VOLUME='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = self.snapshots()[0].parent
        self.assertIn('anonymous-searxng\tanonymous\t', (snapshot / 'volumes.tsv').read_text())
        self.assertIn('name: anonymous-searxng', (snapshot / 'volume-overrides.yaml').read_text())

    def test_anonymous_volumes_shared_with_another_project_are_rejected(self):
        result = self.run_maintenance('--backup', '--non-interactive', ANONYMOUS_VOLUME='1', ANONYMOUS_SHARED='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('stop' in e for e in self.events()))

    def test_missing_old_secret_is_never_regenerated(self):
        (self.root / '.env').write_text(self.original.replace('REDIS_PASSWORD=redis-fixture-secret\n', ''))
        original = (self.root / '.env').read_bytes()
        result = self.run_maintenance('--update', '--local', '--non-interactive')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('REDIS_PASSWORD', result.stderr)
        self.assertEqual((self.root / '.env').read_bytes(), original)
        self.assertFalse(any('stop' in e for e in self.events()))

    def test_project_from_a_different_install_directory_is_never_changed(self):
        result = self.run_maintenance(
            '--update',
            '--local',
            '--non-interactive',
            COMPOSE_PROJECT_NAME='another-project',
            FAKE_WORKING_DIR='/another/installation',
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual((self.root / '.env').read_text(), self.original)
        self.assertFalse(any('stop' in e or e[0] == 'pull' for e in self.events()))

    def test_relocated_installation_keeps_its_existing_compose_project(self):
        (self.root / 'VERSION').write_text('0.12.8\n')
        (self.root / '.env').write_text(
            self.original.replace('v0.12.5', 'v0.12.8') + 'COMPOSE_PROJECT_NAME=legacy-project\n'
        )
        result = self.run_maintenance(
            '--update', '--local', '--non-interactive', FAKE_WORKING_DIR='/original/installation', OLD_VERSION='0.12.8'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('COMPOSE_PROJECT_NAME=legacy-project', (self.root / '.env').read_text())
        self.assertEqual(len(self.snapshots()), 1)

    def test_relocated_legacy_installation_infers_project_from_matching_services(self):
        result = self.run_maintenance(
            '--update', '--local', '--non-interactive', FAKE_WORKING_DIR='/original/installation'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('COMPOSE_PROJECT_NAME=legacy-project', (self.root / '.env').read_text())

    def test_relocated_installation_with_different_secrets_is_rejected_before_changes(self):
        original = self.original + 'COMPOSE_PROJECT_NAME=legacy-project\n'
        original = original.replace('session-fixture-secret', 'different-session-fixture')
        (self.root / '.env').write_text(original)
        result = self.run_maintenance(
            '--update', '--local', '--non-interactive', FAKE_WORKING_DIR='/original/installation'
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('密钥或数据卷', result.stderr)
        self.assertEqual((self.root / '.env').read_text(), original)
        self.assertNotIn('different-session-fixture', result.stdout + result.stderr)
        self.assertFalse(any('stop' in e or e[0] == 'pull' for e in self.events()))

    def test_relocated_installation_rejects_ambiguous_matching_projects(self):
        result = self.run_maintenance(
            '--update', '--local', '--non-interactive', FAKE_WORKING_DIR='/original/installation', AMBIGUOUS_PROJECT='1'
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('COMPOSE_PROJECT_NAME', result.stderr)
        self.assertFalse(any('stop' in e or e[0] == 'pull' for e in self.events()))

    def test_relocated_installation_rejects_custom_data_volume_names(self):
        (self.root / '.env').write_text(self.original + 'COMPOSE_PROJECT_NAME=legacy-project\n')
        result = self.run_maintenance(
            '--update', '--local', '--non-interactive', FAKE_WORKING_DIR='/original/installation', CUSTOM_VOLUME='1'
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('密钥或数据卷', result.stderr)
        self.assertFalse(any('stop' in e or e[0] == 'pull' for e in self.events()))

    def test_relocated_installation_rejects_different_database_with_the_same_password(self):
        original = self.original + 'COMPOSE_PROJECT_NAME=legacy-project\nPOSTGRES_DB=another-database\n'
        (self.root / '.env').write_text(original)
        result = self.run_maintenance(
            '--update', '--local', '--non-interactive', FAKE_WORKING_DIR='/original/installation'
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual((self.root / '.env').read_text(), original)
        self.assertFalse(any('stop' in e or e[0] == 'pull' for e in self.events()))

    def test_working_directory_symlink_is_canonicalized(self):
        alias = Path(self.temp.name) / 'linked installation'
        alias.symlink_to(self.root, target_is_directory=True)
        result = self.run_maintenance('--update', '--local', '--non-interactive', FAKE_WORKING_DIR=str(alias))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_direct_downgrade_is_rejected(self):
        result = self.run_maintenance('--update', '--local', '--non-interactive', OLD_VERSION='0.13.0')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--rollback', result.stderr)
        self.assertFalse(any('stop' in e for e in self.events()))

    def test_cancelled_update_does_not_change_files_or_pull_images(self):
        result = self.run_maintenance('--update', '--local', '--interactive', input='n\n')
        self.assertEqual(result.returncode, 130)
        self.assertEqual((self.root / '.env').read_text(), self.original)
        self.assertFalse(any(e[0] == 'pull' for e in self.events()))

    def test_nonterminal_updates_require_explicit_noninteractive_mode(self):
        result = self.run_maintenance('--update', '--local')
        self.assertEqual(result.returncode, 2)
        self.assertIn('--non-interactive', result.stderr)
        self.assertFalse(self.calls.exists())

    def test_local_target_must_match_requested_version(self):
        result = self.run_maintenance('--update', '--local', '--to', 'v0.12.10', '--non-interactive')
        self.assertEqual(result.returncode, 2)
        self.assertFalse(any(e[0] == 'pull' for e in self.events()))

    def test_same_version_is_noop(self):
        (self.root / 'VERSION').write_text('0.12.9\n')
        result = self.run_maintenance('--update', '--local', '--non-interactive', OLD_VERSION='0.12.9')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any('stop' in e or e[0] == 'pull' for e in self.events()))
        self.assertEqual((self.root / '.env').read_text(), self.original)


if __name__ == '__main__':
    unittest.main()
