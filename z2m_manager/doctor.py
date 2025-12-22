"""
Диагностика системы для Z2M Manager
"""
import os
import sys
import subprocess
import shutil
from pathlib import Path

from .device_detector import DeviceDetector


class DoctorCheck:
    """Результат одной проверки"""
    
    def __init__(self, name: str, ok: bool, message: str, hint: str = ""):
        self.name = name
        self.ok = ok
        self.message = message
        self.hint = hint

    def __str__(self):
        icon = "✅" if self.ok else "❌"
        result = f"{icon} {self.name}: {self.message}"
        if not self.ok and self.hint:
            result += f"\n   💡 {self.hint}"
        return result


def check_python_version() -> DoctorCheck:
    """Проверка версии Python"""
    major, minor = sys.version_info[:2]
    version = f"{major}.{minor}"
    ok = (major, minor) >= (3, 8)
    hint = "Требуется Python 3.8+" if not ok else ""
    return DoctorCheck("Python", ok, f"v{version}", hint)


def check_docker() -> DoctorCheck:
    """Проверка Docker"""
    docker_path = shutil.which("docker")
    if not docker_path:
        return DoctorCheck("Docker", False, "не найден", 
                          "Установите Docker: curl -fsSL https://get.docker.com | sh")
    
    try:
        result = subprocess.run(
            ["docker", "--version"], 
            capture_output=True, text=True, timeout=5
        )
        version = result.stdout.strip().replace("Docker version ", "").split(",")[0]
        return DoctorCheck("Docker", True, f"v{version}")
    except Exception as e:
        return DoctorCheck("Docker", False, f"ошибка: {e}")


def check_docker_compose() -> DoctorCheck:
    """Проверка docker-compose"""
    # Сначала пробуем docker-compose (с дефисом)
    dc_path = shutil.which("docker-compose")
    if dc_path:
        try:
            result = subprocess.run(
                ["docker-compose", "--version"],
                capture_output=True, text=True, timeout=5
            )
            version = result.stdout.strip()
            if "version" in version.lower():
                version = version.split()[-1].strip("v")
            return DoctorCheck("docker-compose", True, f"v{version}")
        except Exception:
            pass
    
    # Пробуем docker compose (как плагин)
    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            version = result.stdout.strip().split()[-1].strip("v")
            return DoctorCheck("docker-compose", True, f"v{version} (плагин)")
    except Exception:
        pass
    
    return DoctorCheck(
        "docker-compose", False, "не найден",
        "Установите: sudo apt install docker-compose-plugin"
    )


def check_docker_group() -> DoctorCheck:
    """Проверка принадлежности к группе docker"""
    try:
        result = subprocess.run(["groups"], capture_output=True, text=True)
        groups = result.stdout.strip().split()
        
        if "docker" in groups:
            return DoctorCheck("Группа docker", True, "пользователь в группе")
        
        return DoctorCheck(
            "Группа docker", False, "пользователь не в группе",
            "Выполните: sudo usermod -aG docker $USER && newgrp docker"
        )
    except Exception as e:
        return DoctorCheck("Группа docker", False, f"ошибка: {e}")


def check_dialout_group() -> DoctorCheck:
    """Проверка принадлежности к группе dialout"""
    try:
        result = subprocess.run(["groups"], capture_output=True, text=True)
        groups = result.stdout.strip().split()
        
        if "dialout" in groups:
            return DoctorCheck("Группа dialout", True, "пользователь в группе")
        
        return DoctorCheck(
            "Группа dialout", False, "пользователь не в группе",
            "Выполните: sudo usermod -aG dialout $USER && перелогиньтесь"
        )
    except Exception as e:
        return DoctorCheck("Группа dialout", False, f"ошибка: {e}")


def check_docker_running() -> DoctorCheck:
    """Проверка что Docker запущен"""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return DoctorCheck("Docker daemon", True, "запущен")
        
        if "permission denied" in result.stderr.lower():
            return DoctorCheck(
                "Docker daemon", False, "нет прав",
                "Добавьте себя в группу docker или запустите с sudo"
            )
        
        return DoctorCheck(
            "Docker daemon", False, "не запущен",
            "Запустите: sudo systemctl start docker"
        )
    except Exception as e:
        return DoctorCheck("Docker daemon", False, f"ошибка: {e}")


def check_usb_device() -> DoctorCheck:
    """Проверка USB устройства"""
    devices = DeviceDetector.detect_serial_devices()
    
    if not devices:
        return DoctorCheck(
            "USB устройства", False, "не обнаружены",
            "Подключите Zigbee USB адаптер"
        )
    
    zigbee_devices = [d for d in devices if d.get('is_zigbee', False)]
    
    if zigbee_devices:
        paths = ", ".join(d['path'] for d in zigbee_devices)
        return DoctorCheck("USB устройства", True, f"найдены: {paths}")
    
    paths = ", ".join(d['path'] for d in devices)
    return DoctorCheck(
        "USB устройства", True, f"найдены (не Zigbee?): {paths}",
        "Проверьте, что подключен именно Zigbee адаптер"
    )


def check_udev_rules() -> DoctorCheck:
    """Проверка udev правил"""
    udev_path = Path("/etc/udev/rules.d/99-zigbee.rules")
    
    if udev_path.exists():
        return DoctorCheck("udev правила", True, "установлены")
    
    # Проверяем есть ли файл в проекте
    project_rules = Path(__file__).parent.parent / "99-zigbee.rules"
    if project_rules.exists():
        return DoctorCheck(
            "udev правила", False, "не установлены",
            f"Установите: sudo cp {project_rules} /etc/udev/rules.d/ && sudo udevadm control --reload-rules"
        )
    
    return DoctorCheck(
        "udev правила", False, "не установлены",
        "Создайте правила для USB устройства"
    )


def check_ports() -> DoctorCheck:
    """Проверка занятости портов"""
    ports_to_check = [
        (1883, "MQTT"),
        (1880, "NodeRED"),
        (4000, "Z2M Frontend"),
    ]
    
    occupied = []
    
    for port, name in ports_to_check:
        try:
            result = subprocess.run(
                ["ss", "-tlnp", f"sport = :{port}"],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip() and "LISTEN" in result.stdout:
                # Порт занят
                occupied.append(f"{port} ({name})")
        except Exception:
            pass
    
    if occupied:
        return DoctorCheck(
            "Порты", False, f"заняты: {', '.join(occupied)}",
            "Остановите процессы на этих портах или измените конфигурацию"
        )
    
    return DoctorCheck("Порты", True, "1883, 1880, 4000 свободны")


def run_doctor(verbose: bool = True) -> list:
    """Запуск всех проверок"""
    checks = [
        check_python_version(),
        check_docker(),
        check_docker_compose(),
        check_docker_running(),
        check_docker_group(),
        check_dialout_group(),
        check_usb_device(),
        check_udev_rules(),
        check_ports(),
    ]
    
    if verbose:
        print("\n🩺 Диагностика системы Z2M")
        print("=" * 50)
        
        for check in checks:
            print(check)
        
        print("=" * 50)
        
        failed = [c for c in checks if not c.ok]
        if failed:
            print(f"\n⚠️ Найдено проблем: {len(failed)}")
            print("Исправьте их перед запуском сервисов.")
        else:
            print("\n✅ Все проверки пройдены!")
            print("Система готова к работе.")
        
        print()
    
    return checks


def is_system_ready() -> tuple:
    """
    Быстрая проверка готовности системы
    Returns: (ready: bool, message: str)
    """
    checks = run_doctor(verbose=False)
    
    critical_checks = [
        "Docker", "docker-compose", "Docker daemon", "Группа docker"
    ]
    
    for check in checks:
        if check.name in critical_checks and not check.ok:
            return False, check.hint or check.message
    
    return True, "Система готова"


