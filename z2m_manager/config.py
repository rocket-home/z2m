"""
Модуль для работы с конфигурацией Z2M окружения
"""
import os
import shutil
import re
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

try:
    from jinja2 import Environment, Undefined  # type: ignore
except Exception:  # pragma: no cover
    Environment = None  # type: ignore
    Undefined = None  # type: ignore


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
        self.templates_dir = self.base_dir / "z2m_manager" / "templates"
        self.zigbee2mqtt_yaml_template = self.templates_dir / "zigbee2mqtt.yaml.j2"
        self.bridge_conf_template = self.templates_dir / "bridge.conf.j2"
        self._config: Dict[str, Any] = {}
        self._env_all: Dict[str, str] = {}
        self.bridge_conf_last_error: Optional[str] = None
        self.load_config()
        self._ensure_local_files()

    def _ensure_local_files(self) -> None:
        """
        Создаёт локальные конфиги, если они отсутствуют.
        Эти файлы намеренно не должны храниться в git:
        - zigbee2mqtt.yaml (после работы UI может содержать ключи/устройства)
        - mosquitto/conf.d/bridge.conf (может содержать креды)
        """
        # На старте создаём локальные файлы (если отсутствуют).
        # Используем только шаблоны (Jinja2). `.example` остаются только для ручной настройки.
        try:
            self.generate_local_configs(force=False, backup=False, zigbee2mqtt_yaml=True, bridge_conf=True, split_yaml=False)
        except Exception:
            # Ничего критичного: просто не создадим файлы автоматически.
            return

    def _read_env_file_all(self) -> Dict[str, str]:
        """Читает .env и возвращает все KEY=VALUE (включая неизвестные)."""
        env: Dict[str, str] = {}
        try:
            if not self.env_file.exists():
                return env
            with open(self.env_file, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key:
                        env[key] = value
        except Exception:
            return env
        return env

    def _get_template_context(self) -> Dict[str, Any]:
        """Контекст для шаблонов: значения из .env + нормализованные значения менеджера."""
        # 1) все ключи из .env (включая пользовательские)
        env_all = self._read_env_file_all()
        self._env_all = env_all
        ctx: Dict[str, Any] = dict(env_all)
        # 2) управляемые ключи (с bool/дефолтами)
        ctx.update(self._config or {})

        # DEVICES_YAML: чтобы генерация zigbee2mqtt.yaml (template) не теряла devices при --force.
        devices_yaml = ""
        if yaml is not None:
            devices: Dict[str, Any] = {}
            # 1) предпочитаем отдельный файл devices
            try:
                if self.zigbee2mqtt_devices_yaml.exists():
                    with open(self.zigbee2mqtt_devices_yaml, "r", encoding="utf-8") as df:
                        d = yaml.safe_load(df) or {}
                    if isinstance(d, dict):
                        devices = d
            except Exception:
                devices = {}
            # 2) fallback: вытаскиваем из текущего zigbee2mqtt.yaml (если отдельного файла нет/пусто)
            if not devices:
                try:
                    if self.zigbee2mqtt_yaml.exists():
                        with open(self.zigbee2mqtt_yaml, "r", encoding="utf-8") as f:
                            cur = yaml.safe_load(f) or {}
                        if isinstance(cur, dict) and isinstance(cur.get("devices"), dict):
                            devices = cur.get("devices") or {}
                except Exception:
                    devices = {}

            if devices:
                try:
                    dumped = yaml.safe_dump(devices, sort_keys=False, allow_unicode=True).rstrip("\n")
                    devices_yaml = dumped
                except Exception:
                    devices_yaml = ""

        ctx["DEVICES_YAML"] = devices_yaml
        return ctx

    def extract_devices_to_file(self, *, backup: bool = True) -> Dict[str, Any]:
        """
        Вынести devices из zigbee2mqtt.yaml в zigbee2mqtt.devices.yaml (+ base без devices).
        zigbee2mqtt.yaml не меняем (он нужен контейнеру напрямую).
        """
        if yaml is None:
            return {"ok": False, "status": "no_yaml_lib", "error": "PyYAML не установлен"}
        if not self.zigbee2mqtt_yaml.exists():
            return {"ok": False, "status": "missing_source", "src": str(self.zigbee2mqtt_yaml)}

        try:
            with open(self.zigbee2mqtt_yaml, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                data = {}
            devices = data.get("devices")
            if not isinstance(devices, dict):
                devices = {}

            base = dict(data)
            base.pop("devices", None)

            bkp_base = None
            bkp_devices = None
            if backup:
                try:
                    if self.zigbee2mqtt_base_yaml.exists():
                        bkp_base = self._backup_file(self.zigbee2mqtt_base_yaml)
                except Exception:
                    bkp_base = None
                try:
                    if self.zigbee2mqtt_devices_yaml.exists():
                        bkp_devices = self._backup_file(self.zigbee2mqtt_devices_yaml)
                except Exception:
                    bkp_devices = None

            with open(self.zigbee2mqtt_base_yaml, "w", encoding="utf-8") as bf:
                yaml.safe_dump(base, bf, sort_keys=False, allow_unicode=True)
            with open(self.zigbee2mqtt_devices_yaml, "w", encoding="utf-8") as df:
                yaml.safe_dump(devices, df, sort_keys=False, allow_unicode=True)

            return {
                "ok": True,
                "status": "written",
                "count": len(devices),
                "base": str(self.zigbee2mqtt_base_yaml),
                "devices": str(self.zigbee2mqtt_devices_yaml),
                "backup_base": str(bkp_base) if bkp_base else None,
                "backup_devices": str(bkp_devices) if bkp_devices else None,
            }
        except Exception as e:
            return {"ok": False, "status": "error", "error": str(e)}

    def _render_template_path(self, template_path: Path, context: Dict[str, Any]) -> str:
        """Рендерит jinja2 шаблон в строку."""
        if Environment is None:
            raise RuntimeError("Jinja2 не установлен")
        text = template_path.read_text(encoding="utf-8")
        env = Environment(  # type: ignore
            autoescape=False,
            undefined=Undefined,  # type: ignore
            keep_trailing_newline=True,
            lstrip_blocks=True,
            trim_blocks=True,
        )
        tpl = env.from_string(text)
        return tpl.render(**context)

    @staticmethod
    def _backup_file(path: Path) -> Optional[Path]:
        """Создаёт backup рядом с файлом: <name>.bak-YYYYmmdd-HHMMSS"""
        if not path.exists():
            return None
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(path.name + f".bak-{ts}")
        shutil.copy2(path, backup)
        return backup

    def generate_local_configs(
        self,
        *,
        force: bool = False,
        backup: bool = True,
        zigbee2mqtt_yaml: bool = True,
        bridge_conf: bool = True,
        split_yaml: bool = False,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Генерация/восстановление локальных конфигов.

        - zigbee2mqtt.yaml: рендерится из z2m_manager/templates/zigbee2mqtt.yaml.j2
        - bridge.conf: рендерится из z2m_manager/templates/bridge.conf.j2
        - split_yaml: (опционально) вынести devices в zigbee2mqtt.devices.yaml (+ base без devices) из zigbee2mqtt.yaml

        Возвращает dict с результатами по каждому действию.
        """
        results: Dict[str, Dict[str, Any]] = {}

        ctx = self._get_template_context()

        def _write_from_template(tpl: Path, dst: Path, key: str) -> None:
            if dst.exists() and not force:
                results[key] = {"ok": True, "status": "skipped_exists", "dst": str(dst)}
                return
            if not tpl.exists():
                results[key] = {"ok": False, "status": "missing_template", "src": str(tpl), "dst": str(dst)}
                return

            bkp: Optional[Path] = None
            if dst.exists() and backup:
                try:
                    bkp = self._backup_file(dst)
                except Exception:
                    bkp = None

            existed_before = dst.exists()
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                rendered = self._render_template_path(tpl, ctx)
                dst.write_text(rendered, encoding="utf-8")
                results[key] = {
                    "ok": True,
                    "status": "overwritten" if existed_before else "created",
                    "src": str(tpl),
                    "dst": str(dst),
                    "backup": str(bkp) if bkp else None,
                }
            except Exception as e:
                results[key] = {"ok": False, "status": "error", "dst": str(dst), "error": str(e)}

        if zigbee2mqtt_yaml:
            _write_from_template(
                self.zigbee2mqtt_yaml_template,
                self.zigbee2mqtt_yaml,
                "zigbee2mqtt.yaml",
            )

        if bridge_conf:
            _write_from_template(
                self.bridge_conf_template,
                self.bridge_conf,
                "bridge.conf",
            )

        if split_yaml:
            results["split_yaml"] = self.extract_devices_to_file(backup=backup)

        return results

    def load_config(self) -> None:
        """Загрузка конфигурации из .env файла"""
        self._config = {
            "MQTT_USER": "user",
            # Дефолтный пароль (пользователь может поменять в настройках).
            "MQTT_PASSWORD": "password",
            # Стабильный путь (создаётся udev-правилами из репозитория)
            "ZIGBEE_DEVICE": "/dev/zigbee",
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

        # Предпочитаем шаблон (Jinja2) чтобы формат был единым с генерацией.
        try:
            ctx = self._get_template_context()
            if self.bridge_conf_template.exists():
                content = self._render_template_path(self.bridge_conf_template, ctx)
            else:
                raise FileNotFoundError("bridge.conf.j2 not found")
        except Exception:
            # fallback: старый формат (как раньше)
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

    def get_z2m_base_topic(self) -> str:
        """
        Возвращает mqtt.base_topic из zigbee2mqtt.yaml.
        Нужен для отправки bridge/request команд (permit_join и др.).
        """
        default = "zigbee2mqtt"
        if yaml is None:
            return default
        try:
            if not self.zigbee2mqtt_yaml.exists():
                return default
            with open(self.zigbee2mqtt_yaml, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                return default
            mqtt = data.get("mqtt")
            if not isinstance(mqtt, dict):
                return default
            base = mqtt.get("base_topic")
            if isinstance(base, str) and base.strip():
                return base.strip()
        except Exception:
            return default
        return default

    def get_z2m_permit_join(self) -> Optional[bool]:
        """Читает permit_join из zigbee2mqtt.yaml (None если не удалось прочитать)."""
        if yaml is None:
            return None
        try:
            if not self.zigbee2mqtt_yaml.exists():
                return None
            with open(self.zigbee2mqtt_yaml, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                return None
            value = data.get("permit_join")
            if isinstance(value, bool):
                return value
            # иногда встречается 0/1
            if isinstance(value, int) and value in (0, 1):
                return bool(value)
            return None
        except Exception:
            return None

    def set_z2m_permit_join(self, enabled: bool) -> bool:
        """Обновляет permit_join в zigbee2mqtt.yaml. Возвращает True/False по результату записи."""
        if yaml is None:
            return False
        if not self.zigbee2mqtt_yaml.exists():
            return False
        try:
            with open(self.zigbee2mqtt_yaml, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                data = {}
            data["permit_join"] = bool(enabled)
            with open(self.zigbee2mqtt_yaml, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
            return True
        except Exception:
            return False

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

