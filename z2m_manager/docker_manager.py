"""
Модуль для работы с Docker Compose и контейнерами Z2M
"""
import subprocess
import json
import os
import shutil
from typing import Dict, List, Optional, Callable
from pathlib import Path

from .config import Z2MConfig


class DockerManager:
    """Класс для управления Docker Compose окружением Z2M"""

    def __init__(self, config: Z2MConfig):
        self.config = config
        self.base_dir = config.base_dir
        self.compose_file = self.base_dir / "docker-compose.yml"
        self._compose_base_cmd = self._detect_compose_cmd()

    def _detect_compose_cmd(self) -> List[str]:
        """
        Возвращает базовую команду для compose.
        Предпочитаем `docker compose` (v2-плагин): get_container_status() использует
        `ps --all --format json`, который настоящий v1 `docker-compose` не поддерживает —
        с ним статус контейнеров всегда читался бы как «остановлено».
        """
        # docker compose plugin (v2) — приоритет
        if shutil.which("docker"):
            try:
                res = subprocess.run(
                    ["docker", "compose", "version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if res.returncode == 0:
                    return ["docker", "compose"]
            except Exception:
                pass
        # fallback: отдельный бинарь docker-compose (может быть v2-обёрткой)
        if shutil.which("docker-compose"):
            return ["docker-compose"]
        # последний fallback: пусть упадёт с понятной ошибкой в логах
        return ["docker-compose"]

    def _get_compose_env(self) -> Dict[str, str]:
        """Формирование переменных окружения для docker-compose"""
        env = os.environ.copy()
        env["MQTT_USER"] = self.config.mqtt_user
        env["MQTT_PASSWORD"] = self.config.mqtt_password
        env["ZIGBEE_DEVICE"] = self.config.zigbee_device
        # host-side device node (docker требует именно char device, symlink не подходит).
        # Приоритет — явно заданный ZIGBEE_DEVICE_HOST в .env (например, после смены донгла,
        # когда host-путь отличается от in-container ZIGBEE_DEVICE), если он указывает на
        # существующий char-device. Иначе выводим из ZIGBEE_DEVICE — той же проверкой
        # char-device, что и Z2MConfig.save_config, чтобы пути не расходились.
        import stat as _stat

        def _char_dev(path: str) -> bool:
            try:
                return bool(path) and os.path.exists(path) and _stat.S_ISCHR(os.stat(path).st_mode)
            except Exception:
                return False

        persisted = (self.config._read_env_file_all() or {}).get("ZIGBEE_DEVICE_HOST", "")
        if _char_dev(persisted):
            env["ZIGBEE_DEVICE_HOST"] = persisted
        else:
            host = self.config.zigbee_device
            try:
                resolved = os.path.realpath(host)
            except Exception:
                resolved = host
            # предпочитаем реальный путь, но если он не существует — оставляем как есть
            env["ZIGBEE_DEVICE_HOST"] = resolved if os.path.exists(resolved) else host
        
        # Добавляем UID/GID текущего пользователя для запуска контейнеров
        env["UID"] = str(os.getuid())
        env["GID"] = str(os.getgid())
        
        return env

    def _get_compose_cmd(self, *args) -> List[str]:
        """Формирование команды docker compose/docker-compose"""
        cmd = [*self._compose_base_cmd, "-f", str(self.compose_file)]

        # Добавляем профили
        for profile in self.config.get_compose_profiles():
            cmd.extend(["--profile", profile])

        cmd.extend(args)
        return cmd

    def _run_compose(
        self,
        args: List[str],
        log_callback: Optional[Callable[[str], None]] = None,
        stream_output: bool = True
    ) -> bool:
        """Запуск docker-compose команды"""
        cmd = self._get_compose_cmd(*args)
        env = self._get_compose_env()

        try:
            if stream_output:
                process = subprocess.Popen(
                    cmd,
                    cwd=str(self.base_dir),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )

                while True:
                    output = process.stdout.readline()
                    if output == '' and process.poll() is not None:
                        break
                    if output:
                        line = output.strip()
                        if log_callback:
                            log_callback(line)
                        else:
                            print(line)

                return process.returncode == 0
            else:
                result = subprocess.run(
                    cmd,
                    cwd=str(self.base_dir),
                    env=env,
                    capture_output=True,
                    text=True
                )
                if log_callback and result.stdout:
                    log_callback(result.stdout)
                if log_callback and result.stderr:
                    log_callback(result.stderr)
                return result.returncode == 0

        except Exception as e:
            if log_callback:
                log_callback(f"❌ Ошибка: {e}")
            return False

    def get_container_status(self) -> Dict[str, Dict[str, str]]:
        """Получение статуса всех контейнеров"""
        cmd = self._get_compose_cmd("ps", "--all", "--format", "json")
        env = self._get_compose_env()

        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.base_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                return {}

            containers = {}
            for line in result.stdout.strip().split('\n'):
                if line:
                    try:
                        data = json.loads(line)
                        service = data.get('Service', data.get('Name', 'unknown'))
                        state = data.get('State', 'unknown')
                        health = data.get('Health', '')
                        status = data.get('Status', '')

                        containers[service] = {
                            'state': state,
                            'health': health,
                            'status': status,
                            'overall': f"{state} ({health})" if health else state
                        }
                    except json.JSONDecodeError:
                        continue

            return containers

        except subprocess.TimeoutExpired:
            return {}
        except Exception:
            return {}

    def is_running(self) -> bool:
        """Проверка, запущены ли контейнеры"""
        status = self.get_container_status()
        if not status:
            return False
        return any(
            info.get('state') == 'running'
            for info in status.values()
        )

    def start_services(self, log_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Запуск всех сервисов"""
        if log_callback:
            log_callback("🚀 Запуск docker compose up -d...")

        device_error = self.config.get_device_error()
        if device_error:
            if log_callback:
                log_callback(f"❌ {device_error}")
                log_callback("💡 Откройте: Настройки → 🔌 Z2M устройство, или выполните: ./z2m set-device /dev/zigbee")
            return False

        # Сначала сохраняем конфигурацию
        self.config.save_config()

        return self._run_compose(["up", "-d", "--build"], log_callback)

    def stop_services(self, log_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Остановка всех сервисов"""
        if log_callback:
            log_callback("🛑 Остановка docker compose stop...")

        return self._run_compose(["stop"], log_callback)

    def restart_services(self, log_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Перезапуск всех сервисов"""
        if log_callback:
            log_callback("🔄 Перезапуск docker compose up -d --force-recreate...")

        device_error = self.config.get_device_error()
        if device_error:
            if log_callback:
                log_callback(f"❌ {device_error}")
                log_callback("💡 Откройте: Настройки → 🔌 Z2M устройство, или выполните: ./z2m set-device /dev/zigbee")
            return False

        # Сначала сохраняем конфигурацию
        self.config.save_config()

        # ВАЖНО: `restart` не пересоздаёт контейнеры и не применяет изменения в devices/env.
        # Поэтому используем `up -d --force-recreate`, чтобы гарантированно применить новый ZIGBEE_DEVICE.
        return self._run_compose(["up", "-d", "--build", "--force-recreate"], log_callback)

    def down_services(self, log_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Полная остановка и удаление контейнеров"""
        if log_callback:
            log_callback("🗑️ Запуск docker compose down...")

        return self._run_compose(["down"], log_callback)

    def down_services_with_volumes(self, log_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Полная остановка и удаление контейнеров вместе с volume (-v)."""
        if log_callback:
            log_callback("💀 Запуск docker compose down -v (удаление volume)...")

        return self._run_compose(["down", "-v"], log_callback)

    def get_logs(
        self,
        service: Optional[str] = None,
        tail: int = 100,
        follow: bool = False
    ) -> subprocess.Popen:
        """Получение логов контейнеров (возвращает процесс для streaming)"""
        args = ["logs", f"--tail={tail}"]

        if follow:
            args.append("--follow")

        if service:
            args.append(service)

        cmd = self._get_compose_cmd(*args)
        env = self._get_compose_env()

        return subprocess.Popen(
            cmd,
            cwd=str(self.base_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

    def get_logs_snapshot(
        self,
        service: Optional[str] = None,
        tail: int = 50
    ) -> str:
        """Получение снимка логов (не следящий режим)"""
        args = ["logs", f"--tail={tail}", "--no-color"]

        if service:
            args.append(service)

        cmd = self._get_compose_cmd(*args)
        env = self._get_compose_env()

        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.base_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=30
            )
            return result.stdout + result.stderr
        except Exception as e:
            return f"Ошибка получения логов: {e}"

    def pull_images(self, log_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Обновление образов"""
        if log_callback:
            log_callback("📦 Загрузка образов...")

        return self._run_compose(["pull"], log_callback)

    def build_images(self, log_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Сборка образов"""
        if log_callback:
            log_callback("🔨 Сборка образов...")

        return self._run_compose(["build"], log_callback)

