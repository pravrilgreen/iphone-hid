"""Why no HDMI input: the device tree, kernel and boot configuration of a board, faked on disk."""

import gzip
import os

import pytest

from ihc.video.diagnose import diagnose

RELEASE = "6.1.115-vendor-rk35xx"


@pytest.fixture
def board(tmp_path):
    base = tmp_path / "sys" / "firmware" / "devicetree" / "base"
    (base / "reserved-memory").mkdir(parents=True)
    (tmp_path / "sys" / "bus" / "platform" / "drivers").mkdir(parents=True)
    (tmp_path / "lib" / "modules" / RELEASE).mkdir(parents=True)
    (tmp_path / "boot").mkdir()
    return tmp_path


def dt_node(board, name, status=None, compatible="rockchip,rk3588-hdmirx-ctrler"):
    node = board / "sys" / "firmware" / "devicetree" / "base" / name
    node.mkdir(parents=True)
    (node / "compatible").write_bytes(compatible.encode() + b"\0")
    if status:
        (node / "status").write_bytes(status.encode() + b"\0")


def verdict(board):
    lines = diagnose(str(board), RELEASE)
    assert lines[-1].startswith("=> ")
    return lines, lines[-1]


def test_no_node(board):
    assert "does not describe" in verdict(board)[1]


def test_node_left_off_by_the_device_tree(board):
    dt_node(board, "hdmirx-controller@fdee0000", "disabled")
    (board / "boot" / "armbianEnv.txt").write_text("verbosity=1\noverlays=panthor-gpu\n")
    lines, v = verdict(board)
    assert "device tree: hdmirx-controller@fdee0000 status=disabled (rockchip,rk3588-hdmirx-ctrler)" in lines
    assert "boot config: armbianEnv.txt: overlays=panthor-gpu" in lines
    assert "leaves it off" in v


def test_armbian_ships_the_overlay(board):
    """Armbian's vendor kernel: rk_hdmirx built in, the Orange Pi 5 Plus node disabled, and the
    rk3588-hdmirx overlay in /boot."""
    dt_node(board, "hdmirx-controller@fdee0000", "disabled")
    dt_node(board, "hdmiin-sound", compatible="rockchip,hdmi")  # enabled, but only the sound card
    (board / "boot" / f"config-{RELEASE}").write_text("CONFIG_VIDEO_ROCKCHIP_HDMIRX=y\n")
    overlays = board / "boot" / "dtb" / "rockchip" / "overlay"
    overlays.mkdir(parents=True)
    for name in ("rk3588-hdmirx.dtbo", "rk3588-i2c0-m1.dtbo"):
        (overlays / name).write_bytes(b"")
    (board / "boot" / "armbianEnv.txt").write_text("overlay_prefix=rk3588\noverlays=\n")
    lines, v = verdict(board)
    assert "overlay available: boot/dtb/rockchip/overlay/rk3588-hdmirx.dtbo" in lines
    assert "kernel config: CONFIG_VIDEO_ROCKCHIP_HDMIRX=y" in lines
    assert not any("hdmiin-sound" in line for line in lines)
    assert "add rk3588-hdmirx to the overlays= line of /boot/armbianEnv.txt" in v


def test_enabled_without_a_driver(board):
    dt_node(board, "hdmirx-controller@fdee0000")  # no status property: enabled
    (board / "proc").mkdir()
    with gzip.open(board / "proc" / "config.gz", "wt") as f:
        f.write("CONFIG_VIDEO_DEV=y\n# CONFIG_VIDEO_ROCKCHIP_HDMIRX is not set\n")
    lines, v = verdict(board)
    assert "kernel config: # CONFIG_VIDEO_ROCKCHIP_HDMIRX is not set" in lines
    assert "no driver" in v


def test_module_not_loaded(board):
    dt_node(board, "hdmirx-controller@fdee0000", "okay")
    mod = board / "lib" / "modules" / RELEASE / "kernel" / "drivers" / "media" / "rockchip_hdmirx.ko"
    mod.parent.mkdir(parents=True)
    mod.write_bytes(b"")
    (board / "proc").mkdir()
    (board / "proc" / "modules").write_text("f_hid 1 0 - Live 0x0\n")
    lines, v = verdict(board)
    assert "module file: kernel/drivers/media/rockchip_hdmirx.ko" in lines
    assert "sudo modprobe rockchip_hdmirx" in v


def test_bound_but_no_video_node(board):
    dt_node(board, "hdmirx-controller@fdee0000", "okay")
    drv = board / "sys" / "bus" / "platform" / "drivers" / "rk_hdmirx"
    drv.mkdir()
    (drv / "bind").write_text("")
    os.symlink(board, drv / "fdee0000.hdmirx-controller")
    lines, v = verdict(board)
    assert "driver rk_hdmirx: bound to fdee0000.hdmirx-controller" in lines
    assert "dmesg" in v


def test_not_a_device_tree_machine(tmp_path):
    assert "no device tree" in diagnose(str(tmp_path), RELEASE)[-1]
