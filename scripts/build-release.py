#!/usr/bin/env python3
"""Package the checked-out release; never include local configuration or backups."""

import argparse
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(output: Path, docker_only: bool = False):
    version = (ROOT / 'VERSION').read_text().strip()
    package = json.loads((ROOT / 'package.json').read_text())
    lock = json.loads((ROOT / 'package-lock.json').read_text())
    assert package['version'] == lock['version'] == lock['packages']['']['version'] == version
    files = (ROOT / 'install-files.txt').read_text().splitlines()
    assert len(set(files)) == len(files) and '.env' not in files
    for name in files:
        path = Path(name)
        assert not path.is_absolute() and '..' not in path.parts
        assert (ROOT / name).is_file() and not (ROOT / name).is_symlink()
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    mtime = int(subprocess.check_output(['git', 'show', '-s', '--format=%ct', 'HEAD'], cwd=ROOT, text=True))
    output.mkdir(parents=True, exist_ok=True)
    prefix = f'nefex-web-ui-v{version}-docker'
    readme = f"""# Nefex Web UI v{version} Docker 安装与更新

需要 Docker Engine/Desktop 和 Compose v2。终端运行 `./scripts/install-docker.sh` 进入安装/维护引导。

```bash
./scripts/install-docker.sh --interactive --init-only
./scripts/install-docker.sh --non-interactive
# 更新已有安装：自动下载、校验最新正式版，保留配置和数据
./scripts/install-docker.sh --update
# 用当前安装包更新旧目录（无需再次下载安装包）
./scripts/install-docker.sh --update --local --install-dir /opt/nefex-web-ui
# 备份、查看备份、恢复
./scripts/install-docker.sh --backup
./scripts/install-docker.sh --list-backups
./scripts/install-docker.sh --rollback /完整备份目录
# 只读检查
./scripts/install-docker.sh --check
```

旧版没有更新功能时，下载 Release 的独立 install.sh，运行：

```bash
bash install.sh --update --install-dir /opt/nefex-web-ui
```

支持现有 Nefex PostgreSQL/MinIO Docker 服务栈（包括 v0.12.4/v0.12.5 和旧项目名）。
更新前暂停服务并备份全部数据卷、配置、镜像引用；失败自动恢复。
备份保存在安装目录 backups/，含密钥和用户数据，权限为 700，请妥善保留。
恢复备份会恢复当时的数据；操作前还会备份当前状态。SQLite/本地文件旧布局需单独迁移。

默认 SearXNG/Playwright；模型和 DeepSeek 密钥可在向导或后台配置。
CPU 支持 amd64/arm64，CUDA 支持 amd64 并需要 Container Toolkit。
完整功能与限制见 docs/docker-install.md。

此为镜像安装包。源码构建需使用完整源码包。二进制更新不会改写你的应用源码，源码与部署版本不同时会阻止误构建。
"""

    def normalize(info):
        info.uid = info.gid = 0
        info.uname = info.gname = ''
        info.mtime = mtime
        return info

    with tarfile.open(output / f'nefex-web-ui-v{version}-docker.tar.gz', 'w:gz') as tar:
        for name in files:
            if name == 'README.md':
                data = readme.encode()
                info = tarfile.TarInfo(prefix + '/' + name)
                info.mode = 0o644
                info.size = len(data)
                tar.addfile(normalize(info), io.BytesIO(data))
            else:
                tar.add(ROOT / name, arcname=prefix + '/' + name, filter=normalize)
    shutil.copy2(ROOT / 'install.sh', output / 'install.sh')
    if not docker_only:
        committed = subprocess.check_output(['git', 'show', 'HEAD:VERSION'], cwd=ROOT, text=True).strip()
        assert committed == version, 'Commit the release before archiving source'
        subprocess.run(
            [
                'git',
                'archive',
                '--format=tar.gz',
                f'--prefix=nefex-web-ui-v{version}-source/',
                '-o',
                str(output / f'nefex-web-ui-v{version}-source.tar.gz'),
                revision,
            ],
            cwd=ROOT,
            check=True,
        )
    print(
        json.dumps(
            {'version': version, 'revision': revision, 'docker_files': len(files), 'output': str(output)}, indent=2
        )
    )


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--docker-only', action='store_true')
    args = parser.parse_args()
    build(args.output.resolve(), args.docker_only)
