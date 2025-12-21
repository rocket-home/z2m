"""
Модуль для работы с конфигурацией Z2M окружения
"""
import os
import shutil
import re
from typing import Dict, Any, Optional
from pathlib import Path

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


class Z2MConfig:
    """Класс для работы с конфигурацией Z2M"""

    DEFAULT_CLOUD_HOST = "mq.rocket-home.ru"
    DEFAULT_CLOUD_PROTOCOL = "mqttv311"  # mqttv31 | mqttv311 | mqttv50
    DEFAULT_FRONTEND_HOST = "0.0.0.0"
    DEFAULT_FRONTEND_PORT = 4000

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or Path(__file__).parent.parent
        self.env_file = self.base_dir / ".env"
        self.bridge_conf = self.base_dir / "mosquitto" / "conf.d" / "bridge.conf"
        self.bridge_conf_example = self.base_dir / "mosquitto" / "conf.d" / "bridge.conf.example"
        self.zigbee2mqtt_yaml = self.base_dir / "zigbee2mqtt.yaml"
        self.zigbee2mqtt_yaml_example = self.base_dir / "zigbee2mqtt.yaml.example"
        self.zigbee2mqtt_base_yaml = self.base_dir / "zigbee2mqtt.base.yaml"
        self.zigbee2mqtt_devices_yaml = self.base_dir / "zigbee2mqtt.devices.yaml"
        self._config: Dict[str, Any] = {}
        self.bridge_conf_last_error: Optional[str] = None
        self._ensure_local_files()
        self.load_config()

    def _ensure_local_files(self) -> None:
        """
        Создаёт локальные конфиги, если они отсутствуют.
        Эти файлы намеренно не должны храниться в git:
        - zigbee2mqtt.yaml (после работы UI может содержать ключи/устройства)
        - mosquitto/conf.d/bridge.conf (может содержать креды)
        """
        # zigbee2mqtt.yaml
        if not self.zigbee2mqtt_yaml.exists():
            if self.zigbee2mqtt_yaml_example.exists():
                try:
                    shutil.copy(self.zigbee2mqtt_yaml_example, self.zigbee2mqtt_yaml)
                except OSError:
                    pass

        # bridge.conf
        self.bridge_conf.parent.mkdir(parents=True, exist_ok=True)
        if not self.bridge_conf.exists():
            if self.bridge_conf_example.exists():
                try:
                    shutil.copy(self.bridge_conf_example, self.bridge_conf)
                except OSError:
                    pass

    def load_config(self) -> None:
        """Загрузка конфигурации из .env файла"""
        self._config = {
            "MQTT_USER": "",
            "MQTT_PASSWORD": "",
            "ZIGBEE_DEVICE": "/dev/ttyACM0",
            "NODERED_ENABLED": False,
            "CLOUD_MQTT_HOST": self.DEFAULT_CLOUD_HOST,
            "CLOUD_MQTT_USER": "",
            "CLOUD_MQTT_PASSWORD": "",
            "CLOUD_MQTT_ENABLED": False,
            "CLOUD_MQTT_PROTOCOL": self.DEFAULT_CLOUD_PROTOCOL,
        }

        if self.env_file.exists():
            with open(self.env_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")

                        if key in self._config:
                            if key == "NODERED_ENABLED":
                                self._config[key] = value.lower() in ('true', '1', 'yes')
                            elif key == "CLOUD_MQTT_ENABLED":
                                self._config[key] = value.lower() in ('true', '1', 'yes')
                            elif key == "CLOUD_MQTT_PROTOCOL":
                                v = value.strip().lower()
                                # допускаем mqttv31/mqttv311/mqttv50
                                if v in ("mqttv31", "mqttv311", "mqttv50"):
                                    self._config[key] = v
                            else:
                                self._config[key] = value

        # Также загружаем cloud MQTT конфигурацию из bridge.conf если есть
        self._load_bridge_config()

    def _load_bridge_config(self) -> None:
        """Загрузка конфигурации MQTT бриджа"""
        if not self.bridge_conf.exists():
            return

        with open(self.bridge_conf, 'r', encoding='utf-8') as f:
            content = f.read()

        # ВАЖНО: CLOUD_MQTT_ENABLED — источник правды в .env.
        # bridge.conf может быть не синхронизирован (например, из-за прав на файл),
        # поэтому здесь НЕ переопределяем флаг включения.
        lines = content.strip().split('\n')

        # Парсим значения (даже если закомментированы)
        for line in lines:
            line = line.lstrip('#').strip()
            if line.startswith('address '):
                self._config["CLOUD_MQTT_HOST"] = line.split(' ', 1)[1].strip()
            elif line.startswith('remote_username '):
                value = line.split(' ', 1)[1].strip()
                # Не загружаем placeholder значения
                if not value.startswith('XXXX'):
                    self._config["CLOUD_MQTT_USER"] = value
            elif line.startswith('remote_password '):
                value = line.split(' ', 1)[1].strip()
                # Не загружаем placeholder значения
                if not value.startswith('XXXX'):
                    self._config["CLOUD_MQTT_PASSWORD"] = value

    def save_config(self) -> None:
        """Сохранение конфигурации в .env файл"""
        self.bridge_conf_last_error = None
        # Template merge: сохраняем неизвестные переменные и комментарии как есть,
        # обновляем только управляемые ключи.
        updates: Dict[str, str] = {
            "MQTT_USER": str(self._config["MQTT_USER"]),
            "MQTT_PASSWORD": str(self._config["MQTT_PASSWORD"]),
            "ZIGBEE_DEVICE": str(self._config["ZIGBEE_DEVICE"]),
            "NODERED_ENABLED": "true" if self._config["NODERED_ENABLED"] else "false",
            "CLOUD_MQTT_HOST": str(self._config["CLOUD_MQTT_HOST"]),
            "CLOUD_MQTT_USER": str(self._config["CLOUD_MQTT_USER"]),
            "CLOUD_MQTT_PASSWORD": str(self._config["CLOUD_MQTT_PASSWORD"]),
            "CLOUD_MQTT_ENABLED": "true" if self._config["CLOUD_MQTT_ENABLED"] else "false",
            "CLOUD_MQTT_PROTOCOL": str(self._config.get("CLOUD_MQTT_PROTOCOL", self.DEFAULT_CLOUD_PROTOCOL)),
        }
        ordered_keys = [
            "MQTT_USER",
            "MQTT_PASSWORD",
            "ZIGBEE_DEVICE",
            "NODERED_ENABLED",
            "CLOUD_MQTT_HOST",
            "CLOUD_MQTT_USER",
            "CLOUD_MQTT_PASSWORD",
            "CLOUD_MQTT_ENABLED",
            "CLOUD_MQTT_PROTOCOL",
        ]

        merged_lines = self._merge_env_file(existing_path=self.env_file, updates=updates, ordered_keys=ordered_keys)
        with open(self.env_file, "w", encoding="utf-8") as f:
            f.write("\n".join(merged_lines).rstrip("\n") + "\n")

        # Обновляем bridge.conf
        ok = self._save_bridge_config()
        if not ok and self.bridge_conf_last_error is None:
            self.bridge_conf_last_error = "Не удалось обновить bridge.conf (проверьте права на файл)"
        
        # Обновляем zigbee2mqtt.yaml
        self._save_zigbee2mqtt_config()

    def _save_bridge_config(self) -> bool:
        """Сохранение конфигурации MQTT бриджа"""
        self.bridge_conf.parent.mkdir(parents=True, exist_ok=True)

        comment_prefix = "" if self._config["CLOUD_MQTT_ENABLED"] else "#"

        # Используем placeholders если значения пустые
        user = self._config['CLOUD_MQTT_USER'] or "XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX"
        password = self._config['CLOUD_MQTT_PASSWORD'] or "XXXXXXXXXX"
        proto = (self._config.get("CLOUD_MQTT_PROTOCOL") or self.DEFAULT_CLOUD_PROTOCOL).strip().lower()
        if proto not in ("mqttv31", "mqttv311", "mqttv50"):
            proto = self.DEFAULT_CLOUD_PROTOCOL

        content = f"""{comment_prefix}connection rocket
{comment_prefix}address {self._config['CLOUD_MQTT_HOST']}
{comment_prefix}bridge_protocol_version {proto}
{comment_prefix}try_private false
{comment_prefix}topic # both 2
{comment_prefix}remote_username {user}
{comment_prefix}remote_password {password}
{comment_prefix}notifications false
"""

        try:
            with open(self.bridge_conf, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        except PermissionError:
            # Если нет прав, пробуем через временный файл и замену
            import tempfile
            try:
                # Создаём временный файл
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False, dir=str(self.bridge_conf.parent)) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                
                # Пробуем скопировать
                shutil.copy(tmp_path, str(self.bridge_conf))
                os.unlink(tmp_path)
                return True
            except (PermissionError, OSError) as e:
                self.bridge_conf_last_error = f"Нет прав на запись {self.bridge_conf} ({e})"
                return False
        except OSError as e:
            self.bridge_conf_last_error = f"Ошибка записи {self.bridge_conf} ({e})"
            return False

    def _save_zigbee2mqtt_config(self) -> None:
        """Обновление zigbee2mqtt.yaml (serial.port + frontend host/port) + вынесение devices в отдельный файл."""
        if not self.zigbee2mqtt_yaml.exists():
            return

        try:
            device = self._config["ZIGBEE_DEVICE"]

            if yaml is None:
                # Без PyYAML не трогаем файл (чтобы не повредить его).
                return

            with open(self.zigbee2mqtt_yaml, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            if not isinstance(data, dict):
                data = {}

            serial = data.get("serial") if isinstance(data.get("serial"), dict) else {}
            serial["port"] = device
            data["serial"] = serial

            frontend = data.get("frontend") if isinstance(data.get("frontend"), dict) else {}
            frontend.setdefault("port", self.DEFAULT_FRONTEND_PORT)
            frontend.setdefault("host", self.DEFAULT_FRONTEND_HOST)
            data["frontend"] = frontend

            # 1) вытаскиваем devices из текущего файла (если есть)
            devices = data.pop("devices", None)
            if devices is None:
                # если уже есть отдельный файл — оставляем его
                if self.zigbee2mqtt_devices_yaml.exists():
                    try:
                        with open(self.zigbee2mqtt_devices_yaml, "r", encoding="utf-8") as df:
                            devices = yaml.safe_load(df) or {}
                    except Exception:
                        devices = {}
                else:
                    devices = {}

            # 2) пишем base и devices
            try:
                with open(self.zigbee2mqtt_base_yaml, "w", encoding="utf-8") as bf:
                    yaml.safe_dump(data, bf, sort_keys=False, allow_unicode=True)
                with open(self.zigbee2mqtt_devices_yaml, "w", encoding="utf-8") as df:
                    yaml.safe_dump(devices, df, sort_keys=False, allow_unicode=True)
            except Exception:
                # если не смогли записать split файлы — просто вернём devices обратно
                data["devices"] = devices
            else:
                # 3) собираем итоговый configuration.yaml для контейнера
                merged = dict(data)
                merged["devices"] = devices
                data = merged

            with open(self.zigbee2mqtt_yaml, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
        except (PermissionError, OSError) as e:
            # Если нет прав, просто пропускаем
            pass

    def get(self, key: str, default: Any = None) -> Any:
        """Получение значения конфигурации"""
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Установка значения конфигурации"""
        self._config[key] = value

    @property
    def mqtt_user(self) -> str:
        return self._config.get("MQTT_USER", "")

    @staticmethod
    def _merge_env_file(existing_path: Path, updates: Dict[str, str], ordered_keys: list[str]) -> list[str]:
        """
        Merge .env:
        - сохраняем все строки как есть (комменты, пустые строки, неизвестные KEY=VALUE)
        - обновляем значения для известных ключей (в первой встреченной строке KEY=...)
        - если ключа нет — добавляем его в конец (в порядке ordered_keys)
        """
        key_re = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")

        if existing_path.exists():
            raw = existing_path.read_text(encoding="utf-8", errors="replace").splitlines()
        else:
            raw = []

        lines = list(raw)
        seen: set[str] = set()

        # 1) обновляем существующие
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            m = key_re.match(line)
            if not m:
                continue
            key = m.group(1)
            if key in updates:
                # обновляем только первое вхождение, остальные оставляем как есть
                if key not in seen:
                    lines[i] = f"{key}={updates[key]}"
                    seen.add(key)

        # 2) добавляем отсутствующие ключи в конец, сохраняя порядок
        to_add = [k for k in ordered_keys if k in updates and k not in seen]
        if to_add:
            if lines and lines[-1].strip() != "":
                lines.append("")
            lines.extend([f"{k}={updates[k]}" for k in to_add])

        return lines

    @mqtt_user.setter
    def mqtt_user(self, value: str) -> None:
        self._config["MQTT_USER"] = value

    @property
    def mqtt_password(self) -> str:
        return self._config.get("MQTT_PASSWORD", "")

    @mqtt_password.setter
    def mqtt_password(self, value: str) -> None:
        self._config["MQTT_PASSWORD"] = value

    @property
    def zigbee_device(self) -> str:
        return self._config.get("ZIGBEE_DEVICE", "/dev/ttyACM0")

    @zigbee_device.setter
    def zigbee_device(self, value: str) -> None:
        self._config["ZIGBEE_DEVICE"] = value

    @property
    def nodered_enabled(self) -> bool:
        return self._config.get("NODERED_ENABLED", False)

    @nodered_enabled.setter
    def nodered_enabled(self, value: bool) -> None:
        self._config["NODERED_ENABLED"] = value

    @property
    def cloud_mqtt_enabled(self) -> bool:
        return self._config.get("CLOUD_MQTT_ENABLED", False)

    @cloud_mqtt_enabled.setter
    def cloud_mqtt_enabled(self, value: bool) -> None:
        self._config["CLOUD_MQTT_ENABLED"] = value

    @property
    def cloud_mqtt_host(self) -> str:
        return self._config.get("CLOUD_MQTT_HOST", self.DEFAULT_CLOUD_HOST)

    @cloud_mqtt_host.setter
    def cloud_mqtt_host(self, value: str) -> None:
        self._config["CLOUD_MQTT_HOST"] = value

    @property
    def cloud_mqtt_user(self) -> str:
        return self._config.get("CLOUD_MQTT_USER", "")

    @cloud_mqtt_user.setter
    def cloud_mqtt_user(self, value: str) -> None:
        self._config["CLOUD_MQTT_USER"] = value

    @property
    def cloud_mqtt_password(self) -> str:
        return self._config.get("CLOUD_MQTT_PASSWORD", "")

    @cloud_mqtt_password.setter
    def cloud_mqtt_password(self, value: str) -> None:
        self._config["CLOUD_MQTT_PASSWORD"] = value

    @property
    def cloud_mqtt_protocol(self) -> str:
        return self._config.get("CLOUD_MQTT_PROTOCOL", self.DEFAULT_CLOUD_PROTOCOL)

    @cloud_mqtt_protocol.setter
    def cloud_mqtt_protocol(self, value: str) -> None:
        v = (value or "").strip().lower()
        self._config["CLOUD_MQTT_PROTOCOL"] = v if v in ("mqttv31", "mqttv311", "mqttv50") else self.DEFAULT_CLOUD_PROTOCOL

    def get_compose_profiles(self) -> list:
        """Получение списка docker-compose профилей"""
        profiles = []
        if self.nodered_enabled:
            profiles.append("nodered")
        return profiles

    def is_configured(self) -> bool:
        """Проверка, настроена ли конфигурация"""
        return bool(self.mqtt_password)

    def get_status_summary(self) -> Dict[str, str]:
        """Получение сводки по конфигурации"""
        return {
            "Zigbee Device": self.zigbee_device,
            "Local MQTT User": self.mqtt_user or "(не задан)",
            "Local MQTT Password": "***" if self.mqtt_password else "(не задан)",
            "NodeRED": "✅ Включен" if self.nodered_enabled else "❌ Выключен",
            "Cloud MQTT": "✅ Включен" if self.cloud_mqtt_enabled else "❌ Выключен",
            "Cloud Host": self.cloud_mqtt_host if self.cloud_mqtt_enabled else "-",
            "Cloud User": self.cloud_mqtt_user if self.cloud_mqtt_enabled else "-",
        }

    def is_device_configured(self) -> bool:
        """Проверка, настроено ли устройство"""
        if not self.zigbee_device:
            return False
        return Path(self.zigbee_device).exists()

    def get_device_error(self) -> Optional[str]:
        """Получение ошибки конфигурации устройства"""
        if not self.zigbee_device:
            return "Zigbee устройство не выбрано"
        if not Path(self.zigbee_device).exists():
            return f"Устройство {self.zigbee_device} не найдено"
        return None

