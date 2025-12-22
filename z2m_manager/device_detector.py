"""
Модуль для автодетекта USB Zigbee адаптеров
"""
import subprocess
import re
from pathlib import Path
from typing import List, Dict, Optional


class DeviceDetector:
    """Класс для обнаружения USB Zigbee адаптеров"""

    # Известные USB ID для Zigbee адаптеров
    KNOWN_DEVICES = {
        "1a86:7523": "CH340 (CC2531, многие клоны)",
        "10c4:ea60": "Silicon Labs CP210x (Sonoff Zigbee 3.0)",
        "10c4:8a2a": "Silicon Labs (EFR32)",
        "0451:16a8": "Texas Instruments CC2531",
        "1cf1:0030": "ConBee/ConBee II",
        "0403:6015": "FTDI (SLZB-06, Tube)",
        "1a86:55d4": "CH9102 (SONOFF ZBDongle-E)",
        "303a:1001": "Espressif (ESP32-based)",
    }

    @classmethod
    def detect_serial_devices(cls) -> List[Dict[str, str]]:
        """Обнаружение всех последовательных устройств"""
        devices = []

        # Проверяем /dev/ttyUSB* и /dev/ttyACM*
        dev_path = Path("/dev")
        patterns = ["ttyUSB*", "ttyACM*"]

        for pattern in patterns:
            for device_path in dev_path.glob(pattern):
                device_info = cls._get_device_info(str(device_path))
                if device_info:
                    devices.append(device_info)

        # Также проверяем /dev/serial/by-id если существует
        serial_by_id = Path("/dev/serial/by-id")
        if serial_by_id.exists():
            for symlink in serial_by_id.iterdir():
                if symlink.is_symlink():
                    real_path = str(symlink.resolve())
                    # Проверяем, не добавили ли мы уже это устройство
                    if not any(d['path'] == real_path for d in devices):
                        device_info = cls._get_device_info(real_path)
                        if device_info:
                            device_info['by_id'] = str(symlink)
                            devices.append(device_info)

        # Если настроен udev-симлинк /dev/zigbee — добавляем его как предпочтительный alias
        zigbee_link = Path("/dev/zigbee")
        if zigbee_link.exists():
            try:
                real_path = str(zigbee_link.resolve())
            except Exception:
                real_path = str(zigbee_link)

            existing = next((d for d in devices if d.get("path") == real_path), None)
            if existing:
                # Показываем /dev/zigbee в UI как предпочтительный вариант выбора
                existing["by_id"] = str(zigbee_link)
            else:
                device_info = cls._get_device_info(real_path)
                if device_info:
                    device_info["by_id"] = str(zigbee_link)
                    devices.append(device_info)

        return devices

    @classmethod
    def _get_device_info(cls, device_path: str) -> Optional[Dict[str, str]]:
        """Получение информации об устройстве"""
        try:
            # Получаем информацию через udevadm
            result = subprocess.run(
                ["udevadm", "info", "--query=all", "--name", device_path],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0:
                return {"path": device_path, "description": "Unknown device"}

            output = result.stdout
            info = {"path": device_path}

            # Извлекаем ID_VENDOR и ID_MODEL
            vendor_match = re.search(r"ID_VENDOR_FROM_DATABASE=(.+)", output)
            model_match = re.search(r"ID_MODEL_FROM_DATABASE=(.+)", output)
            vendor_id_match = re.search(r"ID_VENDOR_ID=(.+)", output)
            product_id_match = re.search(r"ID_MODEL_ID=(.+)", output)

            vendor = vendor_match.group(1) if vendor_match else ""
            model = model_match.group(1) if model_match else ""

            if vendor_id_match and product_id_match:
                usb_id = f"{vendor_id_match.group(1)}:{product_id_match.group(1)}"
                info['usb_id'] = usb_id

                if usb_id in cls.KNOWN_DEVICES:
                    info['is_zigbee'] = True
                    info['description'] = cls.KNOWN_DEVICES[usb_id]
                else:
                    info['is_zigbee'] = False
                    info['description'] = f"{vendor} {model}".strip() or "Unknown USB device"
            else:
                info['is_zigbee'] = False
                info['description'] = f"{vendor} {model}".strip() or "Unknown device"

            return info

        except subprocess.TimeoutExpired:
            return {"path": device_path, "description": "Timeout getting device info"}
        except FileNotFoundError:
            # udevadm не найден, возвращаем базовую информацию
            return {"path": device_path, "description": "Device (udevadm not available)"}
        except Exception as e:
            return {"path": device_path, "description": f"Error: {e}"}

    @classmethod
    def detect_zigbee_adapters(cls) -> List[Dict[str, str]]:
        """Обнаружение только Zigbee адаптеров"""
        all_devices = cls.detect_serial_devices()
        zigbee_devices = [d for d in all_devices if d.get('is_zigbee', False)]

        # Если нет известных Zigbee устройств, возвращаем все serial устройства
        # как потенциальные кандидаты
        if not zigbee_devices:
            return all_devices

        return zigbee_devices

    @classmethod
    def get_default_device(cls) -> str:
        """Получение устройства по умолчанию"""
        # Если есть стабильный симлинк — используем его
        if Path("/dev/zigbee").exists():
            return "/dev/zigbee"

        zigbee_devices = cls.detect_zigbee_adapters()

        if zigbee_devices:
            # Предпочитаем устройства с by_id путём для стабильности
            for device in zigbee_devices:
                if 'by_id' in device:
                    return device['by_id']
            return zigbee_devices[0]['path']

        # Fallback на стандартные пути
        for default_path in ["/dev/ttyACM0", "/dev/ttyUSB0"]:
            if Path(default_path).exists():
                return default_path

        return "/dev/ttyACM0"

