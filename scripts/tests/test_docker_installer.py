"""Installer regression tests; Docker calls are intercepted, no volumes are touched."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SECRETS = ('POSTGRES_PASSWORD', 'REDIS_PASSWORD', 'MINIO_ROOT_PASSWORD', 'WEBUI_SECRET_KEY', 'SEARXNG_SECRET')


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'scripts').mkdir()
        shutil.copytree(REPO / 'scripts/lib', self.root / 'scripts/lib')
        (self.root / 'backend').mkdir()
        shutil.copy(REPO / 'package.json', self.root / 'package.json')
        shutil.copy(REPO / 'scripts/install-docker.sh', self.root / 'scripts/install-docker.sh')
        shutil.copy(REPO / '.env.example', self.root / '.env.example')
        self.calls = self.root / 'calls'
        fake_docker = self.root / 'docker'
        fake_docker.write_text(
            '#!/bin/sh\n'
            'printf "%s\\n" "$*" >> "$DOCKER_CALLS"\n'
            'case "$*" in *"up -d"*) exit "${UP_EXIT:-0}";; esac\n'
            'case "$*" in *"config --quiet"*) exit "${CONFIG_EXIT:-0}";; esac\n'
            'exit 0\n'
        )
        fake_docker.chmod(0o755)
        self.environment = {**os.environ, 'PATH': f'{self.root}:{os.environ["PATH"]}', 'DOCKER_CALLS': str(self.calls)}

    def run_installer(self, *arguments, input=None, **environment):
        return subprocess.run(
            ['bash', str(self.root / 'scripts/install-docker.sh'), *arguments],
            cwd=self.root,
            env={**self.environment, **environment},
            capture_output=True,
            text=True,
            input=input,
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
        self.assertEqual(self.run_installer('--init-only').returncode, 0)
        original = (self.root / '.env').read_bytes()
        self.calls.unlink()
        result = self.run_installer('--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls.read_text()
        self.assertIn('exec -T open-webui python check_stack.py', calls)
        self.assertNotIn(' up ', calls)
        self.assertNotIn(' build', calls)
        self.assertEqual((self.root / '.env').read_bytes(), original)

    def wizard_answers(
        self,
        *,
        runtime='1',
        mode='1',
        port='3000',
        url='http://localhost:3000',
        model=None,
        deepseek=None,
        admin=True,
        email='admin@example.test',
        password='',
        confirm='y',
    ):
        answers = [runtime, mode, '', port, '127.0.0.1', url]
        answers += ['1'] if model is None else ['2', model[0], model[1]]
        answers += ['1'] if deepseek is None else ['2', deepseek[0], deepseek[1]]
        answers += ['2', email, password] if admin else ['1']
        return '\n'.join(answers + [confirm]) + '\n'

    def test_interactive_configuration_creates_private_admin_without_starting(self):
        result = self.run_installer('--interactive', '--init-only', input=self.wizard_answers())
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (self.root / '.env').read_text()
        self.assertIn('WEBUI_ADMIN_EMAIL=admin@example.test', config)
        self.assertRegex(config, r'WEBUI_ADMIN_PASSWORD=[0-9a-f]{48}')
        self.assertIn('ENABLE_SIGNUP=false', config)
        self.assertIn('CORS_ALLOW_ORIGIN=http://localhost:3000', config)
        self.assertIn('[6/6]', result.stdout)
        self.assertNotIn(' up ', self.calls.read_text())
        self.assertEqual((self.root / '.env').stat().st_mode & 0o777, 0o600)

    def test_cancelling_does_not_create_configuration_or_start_services(self):
        result = self.run_installer('--interactive', input=self.wizard_answers(confirm='n'))
        self.assertEqual(result.returncode, 130)
        self.assertFalse((self.root / '.env').exists())
        self.assertFalse([p for p in self.root.glob('.env.*') if p.name != '.env.example'])
        self.assertNotIn(' pull', self.calls.read_text())

    def test_cancelling_preserves_existing_configuration_byte_for_byte(self):
        env_file = self.root / '.env'
        original = b'POSTGRES_PASSWORD=keep-this-secret\nOPEN_WEBUI_PORT=3123\n'
        env_file.write_bytes(original)
        result = self.run_installer('--interactive', input=self.wizard_answers(confirm='n'))
        self.assertEqual(result.returncode, 130)
        self.assertEqual(env_file.read_bytes(), original)
        self.assertFalse([p for p in self.root.glob('.env.*') if p.name != '.env.example'])

    def test_eof_cancels_instead_of_accepting_defaults(self):
        result = self.run_installer('--interactive', input='')
        self.assertEqual(result.returncode, 130)
        self.assertFalse((self.root / '.env').exists())
        self.assertNotIn(' pull', self.calls.read_text())

    def test_invalid_ports_are_reprompted_before_saving(self):
        result = self.run_installer(
            '--interactive', '--init-only', input=self.wizard_answers(port='0\n65536\n3oops\n3000')
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('端口必须是'), 3)
        self.assertIn('OPEN_WEBUI_PORT=3000', (self.root / '.env').read_text())

    def test_public_url_is_an_origin_and_trailing_slash_is_normalized(self):
        result = self.run_installer(
            '--interactive',
            '--init-only',
            input=self.wizard_answers(url='https://example.test/path\nhttps://example.test/'),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (self.root / '.env').read_text()
        self.assertIn('WEBUI_URL=https://example.test\n', config)
        self.assertIn('CORS_ALLOW_ORIGIN=https://example.test\n', config)
        self.assertIn('不包含路径', result.stdout)

    def test_secret_input_is_hidden_and_never_executed(self):
        key = 'sk-$(touch injected) ${UNSET_FIXTURE} # "quoted" \\tail'
        result = self.run_installer(
            '--interactive', '--init-only', input=self.wizard_answers(model=('https://example.test/v1', key))
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(key, result.stdout + result.stderr)
        self.assertFalse((self.root / 'injected').exists())
        again = self.run_installer(
            '--interactive', '--init-only', input=self.wizard_answers(model=('https://example.test/v1', ''))
        )
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertNotIn(key, again.stdout + again.stderr)
        real_docker = shutil.which('docker', path=os.environ['PATH'])
        if real_docker:
            compose_file = self.root / 'dotenv-check.yaml'
            compose_file.write_text(
                'services:\n  check:\n    image: example\n    environment:\n      KEY: ${OPENAI_API_KEY}\n'
            )
            check = subprocess.run(
                [
                    real_docker,
                    'compose',
                    '--project-directory',
                    str(self.root),
                    '--env-file',
                    str(self.root / '.env'),
                    '-f',
                    str(compose_file),
                    'config',
                    '--environment',
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertIn('OPENAI_API_KEY=' + key, check.stdout.splitlines())

    def test_deepseek_selection_saves_shared_credentials(self):
        key = 'deepseek-test-key-only'
        result = self.run_installer(
            '--interactive', '--init-only', input=self.wizard_answers(deepseek=('https://api.deepseek.com/v1', key))
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (self.root / '.env').read_text()
        self.assertIn('WEB_SEARCH_ENGINE=deepseek', config)
        self.assertIn('WEB_LOADER_ENGINE=deepseek', config)
        self.assertIn('DEEPSEEK_SEARCH_API_KEY=' + key, config)
        self.assertNotIn(key, result.stdout + result.stderr)

    def test_empty_deepseek_key_keeps_working_searxng_defaults(self):
        result = self.run_installer(
            '--interactive', '--init-only', input=self.wizard_answers(deepseek=('https://api.deepseek.com', ''))
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (self.root / '.env').read_text()
        self.assertIn('WEB_SEARCH_ENGINE=searxng', config)
        self.assertIn('WEB_LOADER_ENGINE=playwright', config)

    def test_cuda_choice_persists_and_cpu_flag_overrides_it(self):
        result = self.run_installer('--interactive', '--init-only', input=self.wizard_answers(runtime='2'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('NEFEX_INSTALL_GPU=true', (self.root / '.env').read_text())
        self.calls.unlink()
        self.assertEqual(self.run_installer('--init-only').returncode, 0)
        self.assertIn('docker-compose.gpu.yaml', self.calls.read_text())
        self.calls.unlink()
        self.assertEqual(self.run_installer('--cpu', '--init-only').returncode, 0)
        self.assertNotIn('docker-compose.gpu.yaml', self.calls.read_text())

    def test_wizard_source_and_cached_modes_run_only_selected_operations(self):
        for mode, expected in [('2', 'build open-webui'), ('3', ' up ')]:
            self.calls.unlink(missing_ok=True)
            result = self.run_installer('--interactive', input=self.wizard_answers(mode=mode, admin=False))
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = self.calls.read_text()
            self.assertIn(expected, calls)
            if mode == '3':
                self.assertNotIn(' pull', calls)
                self.assertNotIn(' build', calls)
                self.assertIn('--pull never', calls)

    def test_failed_compose_validation_does_not_overwrite_configuration(self):
        env_file = self.root / '.env'
        original = b'POSTGRES_PASSWORD=keep-this-secret\n'
        env_file.write_bytes(original)
        result = self.run_installer('--interactive', input=self.wizard_answers(), CONFIG_EXIT='7')
        self.assertEqual(result.returncode, 7)
        self.assertEqual(env_file.read_bytes(), original)
        self.assertNotIn(' up ', self.calls.read_text())
        self.assertFalse([p for p in self.root.glob('.env.*') if p.name != '.env.example'])

    def test_check_flag_never_prompts_even_with_interactive_flag(self):
        self.assertEqual(self.run_installer('--init-only').returncode, 0)
        self.calls.unlink()
        result = self.run_installer('--interactive', '--check', input='')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('[1/6]', result.stdout)
        self.assertNotIn(' up ', self.calls.read_text())

    def test_image_only_bundle_explains_source_build_requirement(self):
        (self.root / 'package.json').unlink()
        result = self.run_installer('--build')
        self.assertEqual(result.returncode, 2)
        self.assertIn('完整源码包', result.stderr)
        self.assertNotIn(' build', self.calls.read_text())

    def test_check_does_not_generate_a_missing_environment(self):
        result = self.run_installer('--check')
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / '.env').exists())

    def test_export_spacing_comments_and_literal_secrets_survive_rerun(self):
        original = (
            " export POSTGRES_PASSWORD = 'original-password' # retained\n"
            'OPENAI_API_KEY=first\nOPENAI_API_KEY=last # actual value\n'
        )
        (self.root / '.env').write_text(original)
        result = self.run_installer('--init-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(original, (self.root / '.env').read_text())
        self.assertNotIn('POSTGRES_PASSWORD=', (self.root / '.env').read_text())

    def test_active_lock_prevents_a_second_writer(self):
        lock = self.root / '.env.nefex-lock'
        lock.mkdir()
        (lock / 'owner.pid').write_text(str(os.getpid()))
        result = self.run_installer('--init-only')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / '.env').exists())
        self.assertTrue(lock.exists())

    def test_stale_lock_is_recovered(self):
        lock = self.root / '.env.nefex-lock'
        lock.mkdir()
        (lock / 'owner.pid').write_text('99999999')
        result = self.run_installer('--init-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(lock.exists())

    def test_mixed_source_and_distribution_versions_are_not_built(self):
        (self.root / 'VERSION').write_text('0.1.0\n')
        result = self.run_installer('--build')
        self.assertEqual(result.returncode, 2)
        self.assertIn('源码版本不同', result.stderr)
        self.assertNotIn(' build', self.calls.read_text())


if __name__ == '__main__':
    unittest.main()
