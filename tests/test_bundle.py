"""The self-contained box bundle: the self-extracting .run header, the launcher and the installer
scripts (the real bundle is built and smoke-tested under ARM64 emulation by tools/build_bundle.sh and
tools/check_bundle.sh, in the release workflow)."""

import io
import lzma
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "deploy" / "bundle"

pytestmark = pytest.mark.skipif(not shutil.which("xz") or not shutil.which("sh"), reason="needs sh and xz")


@pytest.mark.parametrize("script", ["run-header.sh", "install.sh", "launcher.sh"])
def test_scripts_parse(script):
    subprocess.run(["sh", "-n", str(BUNDLE / script)], check=True)


def make_run(tmp_path: Path, files: dict[str, str]) -> Path:
    """A .run file from the real header and a payload holding `files`."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
    header = (BUNDLE / "run-header.sh").read_text().replace("@VERSION@", "9.9.9+test")
    run = tmp_path / "ihc-box-test.run"
    run.write_bytes(header.encode() + lzma.compress(raw.getvalue(), format=lzma.FORMAT_XZ))
    run.chmod(0o755)
    return run


def sh(run: Path, *args, **env):
    return subprocess.run(["sh", str(run), *args], capture_output=True, text=True,
                          env={**os.environ, "IHC_ANY_ARCH": "1", **env})


def test_run_file_reports_its_version_and_unpacks(tmp_path):
    run = make_run(tmp_path, {"./install.sh": "echo installed\n", "./bin/ihc": "#!/bin/sh\necho hi\n"})
    assert sh(run, "--version").stdout.strip() == "iphone-hid 9.9.9+test (linux-aarch64)"
    assert "install or update" in sh(run, "--help").stdout
    out = tmp_path / "box"
    r = sh(run, "--extract", str(out))
    assert r.returncode == 0, r.stderr
    assert (out / "install.sh").read_text() == "echo installed\n"
    assert os.access(out / "bin" / "ihc", os.X_OK)


def test_run_file_refuses_other_architectures_and_non_root_install(tmp_path):
    run = make_run(tmp_path, {"./install.sh": "echo installed\n"})
    if os.uname().machine != "aarch64":
        r = subprocess.run(["sh", str(run), "--extract", str(tmp_path / "x")], capture_output=True, text=True,
                           env={k: v for k, v in os.environ.items() if k != "IHC_ANY_ARCH"})
        assert r.returncode == 1 and "ARM64" in r.stderr
    if os.geteuid() != 0:
        r = sh(run)
        assert r.returncode == 1 and "sudo" in r.stderr


def test_launcher_picks_the_entry_point_by_name(tmp_path):
    box = tmp_path / "box"
    (box / "bin").mkdir(parents=True)
    shutil.copy(BUNDLE / "launcher.sh", box / "bin" / "ihc")
    (box / "bin" / "ihc").chmod(0o755)
    (box / "bin" / "ihc-hidtest").symlink_to("ihc")
    fake_python = tmp_path / "python"
    fake_python.write_text('#!/bin/sh\necho "$PYTHONPATH|$IHC_TEST_LOG_DIR|$*"\n')
    fake_python.chmod(0o755)
    env = {**os.environ, "IHC_PYTHON": str(fake_python), "HOME": str(tmp_path)}
    main = subprocess.run([str(box / "bin" / "ihc"), "gadget", "status"], capture_output=True, text=True, env=env)
    assert main.stdout.strip() == f"{box}/app:{box}/lib|{tmp_path}/ihc-test-logs|-m ihc.cli gadget status"
    tool = subprocess.run([str(box / "bin" / "ihc-hidtest"), "--gadget"], capture_output=True, text=True, env=env)
    assert tool.stdout.strip().endswith(f"|{box}/app/tools/hidtest.py --gadget")
