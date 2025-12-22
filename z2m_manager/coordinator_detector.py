"""
Определение типа Zigbee координатора (примерно), без запуска Zigbee2MQTT.

Цель: подсказать какой "драйвер" вероятнее всего нужен: zstack vs ember (EZSP).
Это эвристика (по USB VID/PID и строкам udev).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Any

import sys
import shutil
import subprocess
import time
import re


@dataclass
class CoordinatorGuess:
    driver: str  # 'zstack' | 'ember' | 'unknown'
    confidence: str  # 'high' | 'medium' | 'low'
    reason: str


def guess_driver_from_device_info(device_info: Dict[str, str]) -> CoordinatorGuess:
    """
    device_info: результат DeviceDetector._get_device_info + возможные поля:
      - usb_id (vid:pid)
      - description
      - by_id/path
    """
    usb_id = (device_info.get("usb_id") or "").lower()
    desc = (device_info.get("description") or "").lower()
    by_id = (device_info.get("by_id") or "").lower()
    path = (device_info.get("path") or "").lower()

    # Явные сигнатуры
    # - SONOFF ZBDongle-E обычно CH9102 (1a86:55d4) или иные мосты → EFR32 → ember (EZSP)
    if usb_id in {"1a86:55d4", "10c4:8a2a"}:
        return CoordinatorGuess("ember", "high", f"USB ID {usb_id} (часто EFR32 / ZBDongle-E)")

    # - CC2531/CC2652P (TI Z-Stack)
    if usb_id in {"0451:16a8"}:
        return CoordinatorGuess("zstack", "high", f"USB ID {usb_id} (TI CC2531)")

    # CH340-клоны обычно CC2531 → zstack
    if usb_id == "1a86:7523":
        return CoordinatorGuess("zstack", "medium", f"USB ID {usb_id} (CH340, часто CC2531-клон)")

    # CP210x — встречается и там и там, но по нашей базе чаще ZBDongle-P (CC2652) → zstack
    if usb_id == "10c4:ea60":
        # Если в by-id/desc явно EFR/Ember — переопределим
        if "efr" in desc or "ember" in desc or "ezsp" in desc or "zbdongle-e" in desc or "zbdongle-e" in by_id:
            return CoordinatorGuess("ember", "medium", f"USB ID {usb_id} (CP210x) + признаки EFR/Ember")
        return CoordinatorGuess("zstack", "medium", f"USB ID {usb_id} (CP210x, часто CC2652P / ZBDongle-P)")

    # ConBee — не ember/zstack
    if usb_id == "1cf1:0030" or "conbee" in desc:
        return CoordinatorGuess("unknown", "high", "ConBee/ConBee II (не ember/zstack в контексте Zigbee2MQTT)")

    # Доп эвристики по тексту
    if "zbdongle-e" in desc or "zbdongle-e" in by_id:
        return CoordinatorGuess("ember", "medium", "Упоминание ZBDongle-E")
    if "zbdongle-p" in desc or "zbdongle-p" in by_id:
        return CoordinatorGuess("zstack", "medium", "Упоминание ZBDongle-P")
    if "cc2531" in desc or "cc2531" in by_id:
        return CoordinatorGuess("zstack", "medium", "Упоминание CC2531")
    if "cc2652" in desc or "cc2652" in by_id:
        return CoordinatorGuess("zstack", "medium", "Упоминание CC2652")
    if "efr32" in desc or "efr32" in by_id:
        return CoordinatorGuess("ember", "medium", "Упоминание EFR32")

    # Если видим /dev/tty* без контекста — неизвестно
    return CoordinatorGuess("unknown", "low", f"Нет сигнатуры для usb_id={usb_id or '-'} ({path})")


def pick_best_device(devices: list) -> Optional[Dict[str, str]]:
    """Выбираем наиболее вероятный Zigbee-координатор из списка устройств."""
    if not devices:
        return None
    # предпочитаем is_zigbee, затем наличие by_id=/dev/zigbee
    zigbee = [d for d in devices if d.get("is_zigbee")]
    pool = zigbee or devices
    for d in pool:
        if (d.get("by_id") or "") == "/dev/zigbee":
            return d
    return pool[0]


@dataclass
class CoordinatorProbeResult:
    driver: str  # 'zstack' | 'ember' | 'unknown'
    ok: bool
    details: Dict[str, Any]
    message: str


@dataclass
class ToolInstallResult:
    ok: bool
    tool: str
    message: str
    output: str = ""


def install_universal_silabs_flasher() -> ToolInstallResult:
    """
    Пытаемся установить universal-silabs-flasher:
    - предпочитаем pipx (как user-level tool)
    - fallback: ставим в текущий Python env (venv) через pip
    """
    tool = "universal-silabs-flasher"
    existing = shutil.which(tool)
    if existing:
        return ToolInstallResult(ok=True, tool=tool, message=f"Уже установлен: {existing}")

    pipx = shutil.which("pipx")
    if pipx:
        try:
            res = subprocess.run(
                [pipx, "install", "--force", tool],
                capture_output=True,
                text=True,
                timeout=300,
            )
            out = (res.stdout or "") + (res.stderr or "")
            if res.returncode == 0 and shutil.which(tool):
                return ToolInstallResult(ok=True, tool=tool, message="Установлено через pipx", output=out.strip())
            return ToolInstallResult(ok=False, tool=tool, message="pipx не смог установить universal-silabs-flasher", output=out.strip())
        except Exception as e:
            return ToolInstallResult(ok=False, tool=tool, message=f"Ошибка pipx install: {e}")

    # fallback через uv (часто в этом проекте именно так и ставятся зависимости)
    uv = shutil.which("uv")
    if uv:
        try:
            res = subprocess.run(
                [uv, "pip", "install", tool],
                capture_output=True,
                text=True,
                timeout=300,
            )
            out = (res.stdout or "") + (res.stderr or "")
            if res.returncode == 0 and shutil.which(tool):
                return ToolInstallResult(ok=True, tool=tool, message="Установлено через uv pip", output=out.strip())
            if res.returncode != 0:
                return ToolInstallResult(ok=False, tool=tool, message="uv pip install завершился ошибкой", output=out.strip())
        except Exception as e:
            return ToolInstallResult(ok=False, tool=tool, message=f"Ошибка uv pip install: {e}")

    # fallback в текущий Python env (если pip есть)
    try:
        res = subprocess.run(
            [sys.executable, "-m", "pip", "install", tool],
            capture_output=True,
            text=True,
            timeout=300,
        )
        out = (res.stdout or "") + (res.stderr or "")
        if res.returncode == 0 and shutil.which(tool):
            return ToolInstallResult(ok=True, tool=tool, message="Установлено через pip (в текущий Python env)", output=out.strip())
        if "No module named pip" in out:
            return ToolInstallResult(
                ok=False,
                tool=tool,
                message="pip недоступен в текущем Python env (попробуйте установить через pipx: pipx install universal-silabs-flasher)",
                output=out.strip(),
            )
        return ToolInstallResult(ok=False, tool=tool, message="pip install завершился ошибкой", output=out.strip())
    except Exception as e:
        return ToolInstallResult(ok=False, tool=tool, message=f"Не удалось установить (нет pipx/uv и ошибка pip): {e}")


def _znp_fcs(data: bytes) -> int:
    x = 0
    for b in data:
        x ^= b
    return x & 0xFF


def _znp_build(cmd0: int, cmd1: int, payload: bytes = b"") -> bytes:
    length = len(payload) & 0xFF
    header = bytes([length, cmd0 & 0xFF, cmd1 & 0xFF]) + payload
    fcs = _znp_fcs(header)
    return bytes([0xFE]) + header + bytes([fcs])


def _znp_read_frame(ser, timeout_sec: float = 1.0) -> Optional[Dict[str, Any]]:
    """
    Простой парсер ZNP кадра: FE LEN CMD0 CMD1 PAYLOAD FCS
    """
    end = time.time() + timeout_sec
    # Ищем SOF
    while time.time() < end:
        b = ser.read(1)
        if not b:
            continue
        if b[0] == 0xFE:
            break
    else:
        return None

    hdr = ser.read(3)
    if len(hdr) != 3:
        return None
    length, cmd0, cmd1 = hdr[0], hdr[1], hdr[2]
    payload = ser.read(length)
    if len(payload) != length:
        return None
    fcs_b = ser.read(1)
    if len(fcs_b) != 1:
        return None
    fcs = fcs_b[0]
    calc = _znp_fcs(bytes([length, cmd0, cmd1]) + payload)
    if fcs != calc:
        return None
    return {"length": length, "cmd0": cmd0, "cmd1": cmd1, "payload": payload}


def probe_zstack_znp(device: str, baudrate: int = 115200, timeout_sec: float = 1.0) -> CoordinatorProbeResult:
    """
    Активный probe для TI ZNP (Z-Stack): SYS ping + SYS version.
    Это не зависит от Zigbee2MQTT и даёт уверенное определение zstack.
    """
    try:
        import serial  # type: ignore
    except Exception:
        return CoordinatorProbeResult(
            driver="unknown",
            ok=False,
            details={},
            message="Не установлен pyserial (обновите зависимости через ./z2m).",
        )

    # ZNP: SREQ = 0x20, SRSP = 0x60, SYS subsystem = 0x01
    CMD0_SREQ_SYS = 0x20 | 0x01
    CMD0_SRSP_SYS = 0x60 | 0x01
    CMD1_PING = 0x01
    CMD1_VERSION = 0x02

    try:
        with serial.Serial(device, baudrate=baudrate, timeout=0.2) as ser:
            # Сброс буферов
            try:
                ser.reset_input_buffer()
                ser.reset_output_buffer()
            except Exception:
                pass

            # ping (capabilities=1)
            ping_req = _znp_build(CMD0_SREQ_SYS, CMD1_PING, payload=bytes([0x01]))
            for _ in range(3):
                ser.write(ping_req)
                ser.flush()
                frame = _znp_read_frame(ser, timeout_sec=timeout_sec)
                if frame and frame["cmd0"] == CMD0_SRSP_SYS and frame["cmd1"] == CMD1_PING:
                    break
            else:
                return CoordinatorProbeResult(
                    driver="unknown",
                    ok=False,
                    details={},
                    message="ZNP ping не ответил (возможно это не zstack или неверная скорость/порт).",
                )

            # version
            ver_req = _znp_build(CMD0_SREQ_SYS, CMD1_VERSION, payload=b"")
            ser.write(ver_req)
            ser.flush()

            frame = _znp_read_frame(ser, timeout_sec=timeout_sec)
            if not frame or frame["cmd0"] != CMD0_SRSP_SYS or frame["cmd1"] != CMD1_VERSION:
                return CoordinatorProbeResult(
                    driver="zstack",
                    ok=True,
                    details={"ping": "ok"},
                    message="ZNP ping ok, но version не ответил (старая прошивка или ограниченный ответ).",
                )

            payload: bytes = frame["payload"]
            # Часто первый байт статус (0=SUCCESS)
            parsed: Dict[str, Any] = {"raw": payload.hex()}
            if len(payload) >= 7 and payload[0] in (0, 1, 2, 3):
                status = payload[0]
                parsed["status"] = status
                off = 1
            else:
                off = 0

            if len(payload) >= off + 5:
                parsed["transportrev"] = payload[off + 0]
                parsed["product"] = payload[off + 1]
                parsed["majorrel"] = payload[off + 2]
                parsed["minorrel"] = payload[off + 3]
                parsed["maintrel"] = payload[off + 4]
            if len(payload) >= off + 9:
                # revision обычно uint32 LE
                rev = int.from_bytes(payload[off + 5:off + 9], "little", signed=False)
                parsed["revision"] = rev

            return CoordinatorProbeResult(
                driver="zstack",
                ok=True,
                details={"version": parsed},
                message="ZNP probe успешен",
            )
    except Exception as e:
        msg = str(e)
        hint = ""
        lower = msg.lower()
        if "multiple access" in lower or "resource busy" in lower or "device disconnected" in lower:
            hint = " (порт занят — остановите Zigbee2MQTT/контейнеры и повторите: ./z2m stop)"
        return CoordinatorProbeResult(driver="unknown", ok=False, details={}, message=f"Ошибка probe zstack: {msg}{hint}")


def probe_silabs_firmware(device: str) -> CoordinatorProbeResult:
    """
    Не-спекулятивный probe для Silicon Labs (EFR32): через universal-silabs-flasher, если установлен.
    """
    usf = shutil.which("universal-silabs-flasher")
    if not usf:
        return CoordinatorProbeResult(
            driver="unknown",
            ok=False,
            details={},
            message="universal-silabs-flasher не установлен (установите: ./z2m coordinator --install-usf).",
        )

    try:
        # Формат вывода зависит от версии; возвращаем stdout как есть + пытаемся вытащить версию.
        result = subprocess.run(
            [usf, "--device", device, "info"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        out = (result.stdout or "") + (result.stderr or "")
        ok = result.returncode == 0
        firmware_version = None
        if ok and out:
            m = re.search(
                r"(firmware|fw)\s*version\s*[:=]\s*([0-9]+(?:\.[0-9]+){1,4})",
                out,
                re.IGNORECASE,
            )
            if m:
                firmware_version = m.group(2)
        return CoordinatorProbeResult(
            driver="ember" if ok else "unknown",
            ok=ok,
            details={"firmware": firmware_version, "output": out.strip()},
            message="Silabs info получено" if ok else "Не удалось получить info через universal-silabs-flasher",
        )
    except Exception as e:
        return CoordinatorProbeResult(driver="unknown", ok=False, details={}, message=f"Ошибка probe silabs: {e}")


def probe_coordinator(device_info: Dict[str, str], device_path: str) -> CoordinatorProbeResult:
    """
    Полный probe: сначала пробуем zstack (ZNP), затем Silabs tool.
    """
    # 1) ZNP probe (TI)
    znp = probe_zstack_znp(device_path)
    if znp.ok and znp.driver == "zstack":
        return znp

    # 2) Silabs probe (Ember/EZSP) через внешний tool
    usb_id = (device_info.get("usb_id") or "").lower()
    if usb_id.startswith("10c4:") or usb_id.startswith("1a86:55d4") or "efr32" in (device_info.get("description") or "").lower():
        return probe_silabs_firmware(device_path)

    # 3) fallback
    return znp


