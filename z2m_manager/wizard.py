"""
Wizard для первой настройки Z2M Manager
"""
import os
import sys
from pathlib import Path

from .device_detector import DeviceDetector
from .config import Z2MConfig
from .mqtt_test import test_mqtt_connection


def colored(text: str, color: str) -> str:
    """Простая раскраска текста"""
    colors = {
        'green': '\033[92m',
        'yellow': '\033[93m',
        'red': '\033[91m',
        'blue': '\033[94m',
        'cyan': '\033[96m',
        'bold': '\033[1m',
        'reset': '\033[0m',
    }
    return f"{colors.get(color, '')}{text}{colors['reset']}"


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    """Запрос да/нет"""
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            answer = input(f"{prompt} {hint}: ").strip().lower()
            if not answer:
                return default
            if answer in ('y', 'yes', 'д', 'да'):
                return True
            if answer in ('n', 'no', 'н', 'нет'):
                return False
            print("  Введите y(да) или n(нет)")
        except (EOFError, KeyboardInterrupt):
            print()
            return default


def ask_choice(prompt: str, options: list, default: int = 0) -> int:
    """Выбор из списка"""
    print(f"\n{prompt}")
    for i, opt in enumerate(options):
        marker = "→" if i == default else " "
        print(f"  {marker} {i + 1}. {opt}")
    
    while True:
        try:
            hint = f"[1-{len(options)}, по умолчанию {default + 1}]"
            answer = input(f"Выбор {hint}: ").strip()
            if not answer:
                return default
            num = int(answer)
            if 1 <= num <= len(options):
                return num - 1
            print(f"  Введите число от 1 до {len(options)}")
        except ValueError:
            print("  Введите число")
        except (EOFError, KeyboardInterrupt):
            print()
            return default


def run_wizard() -> bool:
    """
    Запуск мастера первой настройки.
    Returns: True если настройка завершена, False если отменена
    """
    print()
    print(colored("═" * 50, "cyan"))
    print(colored("  🐝 Добро пожаловать в Z2M Manager!", "bold"))
    print(colored("═" * 50, "cyan"))
    print()
    print("Похоже, это первый запуск. Давайте настроим систему.")
    print()
    
    config = Z2MConfig()
    
    # === Шаг 0: Доступ к USB ===
    print(colored("━━━ Шаг 1/4: Доступ к USB ━━━", "blue"))
    print("\nЕсли Zigbee адаптер не виден в /dev — обычно нужно:")
    print("- добавить пользователя в группу dialout")
    print("- установить udev-правила (создают /dev/zigbee)")
    print()

    if ask_yes_no("Выполнить настройку доступа к USB сейчас?", default=False):
        rules_src = Path(__file__).parent.parent / "99-zigbee.rules"
        if not rules_src.exists():
            print(colored(f"❌ Не найден файл правил: {rules_src}", "red"))
        else:
            print("\nБудут выполнены команды с sudo:")
            print("  sudo usermod -aG dialout $USER")
            print(f"  sudo cp {rules_src} /etc/udev/rules.d/99-zigbee.rules")
            print("  sudo udevadm control --reload-rules")
            print("  sudo udevadm trigger")
            print()
            try:
                os.system("/bin/bash -lc 'sudo usermod -aG dialout \"$USER\"'")
                os.system(f"/bin/bash -lc 'sudo cp {str(rules_src)!r} /etc/udev/rules.d/99-zigbee.rules'")
                os.system("/bin/bash -lc 'sudo udevadm control --reload-rules && sudo udevadm trigger'")
                os.system("/bin/bash -lc 'ls -la /dev/ttyUSB* /dev/ttyACM* /dev/zigbee 2>/dev/null || true'")
                print("\nℹ️ Если dialout был добавлен только что — перелогиньтесь или выполните: newgrp dialout")
            except Exception as e:
                print(colored(f"❌ Ошибка выполнения команд: {e}", "red"))

        input("\nНажмите Enter для продолжения...")

    # === Шаг 1: Выбор USB устройства ===
    print()
    print(colored("━━━ Шаг 2/4: Zigbee USB адаптер ━━━", "blue"))
    
    devices = DeviceDetector.detect_serial_devices()
    zigbee_devices = [d for d in devices if d.get('is_zigbee', False)]
    
    if zigbee_devices:
        print(f"\n✅ Обнаружен Zigbee адаптер:")
        for d in zigbee_devices:
            print(f"   {d['path']} - {d.get('description', 'Unknown')}")
        
        if len(zigbee_devices) == 1:
            device = zigbee_devices[0]
            device_path = device.get('by_id', device['path'])
            if ask_yes_no(f"\nИспользовать {device_path}?"):
                config.zigbee_device = device_path
            else:
                print("\n⚠️ Выберите устройство позже в настройках.")
        else:
            options = [f"{d['path']} - {d.get('description', '')}" for d in zigbee_devices]
            choice = ask_choice("Выберите устройство:", options)
            device = zigbee_devices[choice]
            config.zigbee_device = device.get('by_id', device['path'])
    
    elif devices:
        print(f"\n⚠️ Найдены USB устройства, но они не распознаны как Zigbee:")
        for d in devices:
            print(f"   {d['path']} - {d.get('description', 'Unknown')}")
        
        options = [f"{d['path']} - {d.get('description', '')}" for d in devices]
        options.append("Пропустить (настрою позже)")
        choice = ask_choice("Выберите устройство или пропустите:", options, default=len(options) - 1)
        
        if choice < len(devices):
            device = devices[choice]
            config.zigbee_device = device.get('by_id', device['path'])
    
    else:
        print("\n❌ USB устройства не найдены!")
        print("   Подключите Zigbee адаптер и настройте его позже.")
        input("\nНажмите Enter для продолжения...")
    
    # === Шаг 2: NodeRED ===
    print()
    print(colored("━━━ Шаг 3/4: NodeRED ━━━", "blue"))
    print("\nNodeRED — визуальный редактор автоматизаций.")
    print("Полезен для сложных сценариев, но необязателен.")
    
    config.nodered_enabled = ask_yes_no("\nВключить NodeRED?", default=False)
    
    if config.nodered_enabled:
        print("✅ NodeRED будет доступен на http://localhost:1880")
    else:
        print("ℹ️ NodeRED выключен (можно включить позже)")
    
    # === Шаг 3: Облачный MQTT ===
    print()
    print(colored("━━━ Шаг 4/4: Облачный MQTT ━━━", "blue"))
    print("\nОблачный MQTT позволяет управлять устройствами удалённо.")
    print("Требуется регистрация на mq.rocket-home.ru")
    
    wants_cloud = ask_yes_no("\nНастроить облачный MQTT?", default=False)
    
    if wants_cloud:
        print("\nВведите данные для подключения:")
        
        host = input(f"Хост [{config.cloud_mqtt_host}]: ").strip()
        if host:
            config.cloud_mqtt_host = host
        
        user = input("UUID пользователя: ").strip()
        if user:
            config.cloud_mqtt_user = user
        
        password = input("Пароль: ").strip()
        if password:
            config.cloud_mqtt_password = password

        # Тестируем подключение (без публикаций)
        print()
        if ask_yes_no("Проверить подключение к облачному MQTT сейчас?", default=True):
            print("⏳ Проверяю подключение...")
            test = test_mqtt_connection(
                host=config.cloud_mqtt_host,
                username=config.cloud_mqtt_user,
                password=config.cloud_mqtt_password,
                port=1883,
                timeout_sec=5,
            )

            if test.ok:
                print(colored(f"✅ {test.message} ({test.host}:{test.port})", "green"))
                # Предлагаем включить
                config.cloud_mqtt_enabled = ask_yes_no("Включить облачный MQTT (бридж) сейчас?", default=True)
                if config.cloud_mqtt_enabled:
                    print("✅ Облачный MQTT будет включён")
                else:
                    print("ℹ️ Облачный MQTT сохранён, но выключен (можно включить позже)")
            else:
                print(colored(f"❌ {test.message} ({test.host}:{test.port})", "red"))
                print("ℹ️ Креды сохранены, но Cloud MQTT оставлен выключенным.")
                print("   Проверьте данные в профиле: https://rocket-home.ru/profile/mqtt")
                config.cloud_mqtt_enabled = False
        else:
            # Без теста — выключено по умолчанию, чтобы не ломать запуск
            config.cloud_mqtt_enabled = False
            print("ℹ️ Креды сохранены. Cloud MQTT выключен (включите позже в настройках).")
    else:
        config.cloud_mqtt_enabled = False
        print("ℹ️ Облачный MQTT выключен (можно настроить позже)")
    
    # === Сохранение ===
    print()
    print(colored("━━━ Завершение настройки ━━━", "blue"))
    
    config.save_config()
    print("\n✅ Конфигурация сохранена!")
    
    # === Итоговая информация ===
    print()
    print(colored("═" * 50, "cyan"))
    print(colored("  📋 Итоговая конфигурация:", "bold"))
    print(colored("═" * 50, "cyan"))
    
    summary = config.get_status_summary()
    for key, value in summary.items():
        print(f"  {key}: {value}")
    
    print()
    
    # === Предложение запустить ===
    device_error = config.get_device_error()
    if device_error:
        print(f"⚠️ {device_error}")
        print("Запуск сервисов невозможен без настроенного устройства.")
        return True
    
    if ask_yes_no("Запустить сервисы сейчас?"):
        return "start"
    
    print()
    print("ℹ️ Для запуска выполните: ./z2m start")
    print("   Или запустите TUI:     ./z2m")
    
    return True


def is_first_run() -> bool:
    """Проверка первого запуска"""
    # Считаем первым запуском если нет .env файла
    base_dir = Path(__file__).parent.parent
    env_file = base_dir / ".env"
    return not env_file.exists()


def maybe_run_wizard(skip: bool = False) -> str:
    """
    Запуск wizard если это первый запуск.
    Returns: 'continue' | 'start' | 'exit'
    """
    if skip:
        return 'continue'
    
    if not is_first_run():
        return 'continue'
    
    # Проверяем что это интерактивный терминал
    if not sys.stdin.isatty():
        return 'continue'
    
    # Спрашиваем хочет ли пользователь пройти настройку
    print()
    print(colored("🐝 Первый запуск Z2M Manager", "bold"))
    print()
    
    if not ask_yes_no("Запустить мастер настройки?", default=True):
        print("\nℹ️ Настройку можно запустить позже: ./z2m --wizard")
        print("   Или настройте параметры в TUI/CLI\n")
        # Создаём пустой .env чтобы wizard больше не предлагался
        base_dir = Path(__file__).parent.parent
        env_file = base_dir / ".env"
        env_file.touch()
        return 'continue'
    
    try:
        result = run_wizard()
        if result == "start":
            return 'start'
        return 'continue'
    except KeyboardInterrupt:
        print("\n\nНастройка прервана. Запустите ./z2m --wizard для настройки.")
        return 'exit'

