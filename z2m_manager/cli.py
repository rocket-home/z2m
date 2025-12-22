"""
Консольный интерфейс для управления Z2M окружением
"""
import sys
from typing import Optional

from .config import Z2MConfig
from .docker_manager import DockerManager
from .device_detector import DeviceDetector
from .doctor import run_doctor
from .wizard import maybe_run_wizard, run_wizard
from .coordinator_detector import (
    guess_driver_from_device_info,
    pick_best_device,
    probe_coordinator,
    install_universal_silabs_flasher,
)
from .mqtt_test import set_z2m_permit_join as set_z2m_permit_join_runtime


class Z2MCLI:
    """CLI интерфейс для управления Z2M"""

    def __init__(self):
        try:
            self.config = Z2MConfig()
            self.docker_manager = DockerManager(self.config)
        except Exception as e:
            print(f"❌ Ошибка инициализации: {e}")
            sys.exit(1)

    def show_help(self):
        """Показать справку"""
        print("""
🐝 Z2M Manager - Управление Zigbee2MQTT окружением

Доступные команды:

📊 Статус и информация:
  status, s          - Показать статус контейнеров
  ps, containers     - Показать статус контейнеров (алиас)
  config, c          - Показать текущую конфигурацию
  devices, d         - Показать доступные USB устройства
  coordinator        - Определить тип координатора (ember/zstack) по USB
  coordinator --probe [dev] - Активный probe порта (zstack точно; silabs через tool)
  permit-join        - Разрешить/запретить подключение новых устройств (permit_join) в zigbee2mqtt.yaml

🐳 Управление контейнерами:
  start              - Запустить все сервисы
  stop               - Остановить все сервисы
  restart            - Перезапустить все сервисы
  down               - Полная остановка (удалить контейнеры)
  logs [service]     - Показать логи (mqtt/zigbee2mqtt/nodered)
  logs -f [service]  - Следить за логами (Ctrl+C чтобы выйти)

⚙️ Настройка:
  set-device <path>  - Установить Zigbee устройство
  set-mqtt-user <u>  - Установить MQTT пользователя
  set-mqtt-pass <p>  - Установить MQTT пароль
  enable-nodered     - Включить NodeRED
  disable-nodered    - Выключить NodeRED
  enable-cloud       - Включить облачный MQTT
  disable-cloud      - Выключить облачный MQTT
  set-cloud-host <h> - Установить хост облачного MQTT
  set-cloud-user <u> - Установить пользователя облачного MQTT
  set-cloud-pass <p> - Установить пароль облачного MQTT

❓ Справка:
  help, h            - Показать эту справку
  exit, quit, q      - Выйти
        """)

    def cmd_status(self, compact: bool = False):
        """Показать статус контейнеров"""
        status = self.docker_manager.get_container_status()

        if compact:
            # Однострочный компактный вывод
            if not status:
                print("⚫ (не запущено)")
                return
            parts = []
            for service, info in status.items():
                state = info.get('overall', 'unknown')
                if 'running' in state.lower():
                    parts.append(f"🟢 {service}")
                elif 'exited' in state.lower():
                    parts.append(f"🔴 {service}")
                else:
                    parts.append(f"🟡 {service}")
            print("  ".join(parts))
            return

        print("\n📊 Статус контейнеров:")
        print("-" * 50)

        if not status:
            print("  (контейнеры не запущены)")
            return

        for service, info in status.items():
            state = info.get('overall', 'unknown')
            if 'running' in state.lower():
                icon = "✅"
            elif 'exited' in state.lower():
                icon = "❌"
            else:
                icon = "⚠️"
            print(f"  {icon} {service}: {state}")

    def cmd_config(self):
        """Показать конфигурацию"""
        print("\n⚙️ Текущая конфигурация:")
        print("-" * 50)

        summary = self.config.get_status_summary()
        for key, value in summary.items():
            print(f"  {key}: {value}")

    def cmd_devices(self):
        """Показать доступные устройства"""
        print("\n🔌 Доступные USB устройства:")
        print("-" * 50)

        devices = DeviceDetector.detect_serial_devices()

        if not devices:
            print("  (устройства не найдены)")
            print("\n  Подключите Zigbee USB адаптер")
            return

        for device in devices:
            path = device['path']
            desc = device.get('description', 'Unknown')
            is_zigbee = device.get('is_zigbee', False)
            by_id = device.get('by_id', '')

            icon = "⚡" if is_zigbee else "📟"
            print(f"  {icon} {path}")
            print(f"      {desc}")
            if by_id:
                print(f"      by-id: {by_id}")

    def cmd_permit_join(self, args: list[str]) -> None:
        """
        Управление permit_join в zigbee2mqtt.yaml (персистентно).
        Опционально: --mqtt для runtime-команды через MQTT (не меняет yaml).
        Примеры:
          permit-join on
          permit-join off
          permit-join on --mqtt 60
        """
        if not args:
            cur = self.config.get_z2m_permit_join()
            cur_s = "неизвестно" if cur is None else ("ВКЛ" if cur else "ВЫКЛ")
            print(f"permit_join: {cur_s}")
            print("Использование: permit-join on|off [--mqtt [сек]]")
            return

        mqtt_mode = "--mqtt" in args or "--runtime" in args
        args_wo_flags = [a for a in args if a not in ("--mqtt", "--runtime")]

        if not args_wo_flags:
            print("❌ Укажите on|off")
            return

        action = args_wo_flags[0].strip().lower()
        if action in ("on", "enable", "1", "true", "yes"):
            enabled = True
        elif action in ("off", "disable", "0", "false", "no"):
            enabled = False
        else:
            print("❌ Неверное действие. Используйте: on|off [--mqtt [сек]]")
            return

        if mqtt_mode:
            duration = 60
            if enabled and len(args_wo_flags) > 1 and args_wo_flags[1].strip().isdigit():
                duration = int(args_wo_flags[1].strip())
            res = set_z2m_permit_join_runtime(self.config, enabled=enabled, duration_sec=duration)
            if res.ok:
                if enabled:
                    print(f"✅ permit_join runtime включен на {duration} сек (topic: {res.topic})")
                else:
                    print(f"✅ permit_join runtime выключен (topic: {res.topic})")
            else:
                print(f"❌ permit_join runtime: {res.message}")
            return

        ok = self.config.set_z2m_permit_join(enabled)
        if ok:
            print(f"✅ permit_join в zigbee2mqtt.yaml: {'ВКЛ' if enabled else 'ВЫКЛ'}")
        else:
            print("❌ Не удалось обновить zigbee2mqtt.yaml (проверьте файл и права)")

        print()
        current = self.config.zigbee_device
        device_error = self.config.get_device_error()
        if device_error:
            print(f"  ⚠️ {device_error}")
        else:
            print(f"  Текущий выбор: {current}")

    def cmd_coordinator(self, args: Optional[list] = None):
        """Определить драйвер координатора (ember/zstack) по USB эвристике."""
        args = args or []
        do_probe = False
        do_install_usf = False
        device_override: Optional[str] = None
        for a in args:
            if a in ("--probe", "-p"):
                do_probe = True
            elif a in ("--install-usf", "--install-flasher"):
                do_install_usf = True
            elif not a.startswith("-"):
                device_override = a

        print("\n🧩 Координатор (оценка драйвера):")
        print("-" * 50)

        devices = DeviceDetector.detect_serial_devices()
        device = None
        if device_override:
            # Попробуем найти device_info по реальному пути
            for d in devices:
                if d.get("by_id") == device_override or d.get("path") == device_override:
                    device = d
                    break
            if device is None:
                device = {"path": device_override, "by_id": device_override, "description": "Manual device"}
        else:
            device = pick_best_device(devices)
        if not device:
            print("  (устройства не найдены)")
            return

        device_path = device_override or (device.get("by_id") or device.get("path"))

        if do_install_usf:
            print("  Установка: universal-silabs-flasher")
            inst = install_universal_silabs_flasher()
            print(f"  Результат: {'✅' if inst.ok else '❌'} {inst.message}")
            if inst.output:
                print("  ---")
                print(inst.output)
                print("  ---")
            if not do_probe:
                return

        if do_probe:
            print("  Режим: probe")
            res = probe_coordinator(device, device_path)
            print(f"  Устройство: {device_path}")
            print(f"  Результат: {'✅' if res.ok else '❌'} {res.driver}")
            print(f"  Сообщение: {res.message}")
            if res.details:
                # печатаем кратко
                # zstack: details["version"] dict, ember: details["firmware"]
                ver = res.details.get("version") if isinstance(res.details, dict) else None
                fw = res.details.get("firmware") if isinstance(res.details, dict) else None
                if isinstance(ver, dict):
                    rev = ver.get("revision")
                    maj = ver.get("majorrel")
                    minr = ver.get("minorrel")
                    maint = ver.get("maintrel")
                    print(f"  firmware(znp): rev={rev} ver={maj}.{minr}.{maint}")
                if fw:
                    print(f"  firmware(ember): {fw}")
                for k, v in res.details.items():
                    if k in ("version", "output", "firmware"):
                        continue
                    print(f"  {k}: {v}")
            return

        guess = guess_driver_from_device_info(device)
        shown_path = device.get("by_id") or device.get("path")
        usb_id = device.get("usb_id", "-")
        desc = device.get("description", "Unknown")

        print(f"  Устройство: {shown_path}")
        print(f"  USB ID: {usb_id}")
        print(f"  Описание: {desc}")
        print()
        print(f"  Драйвер: {guess.driver}")
        print(f"  Уверенность: {guess.confidence}")
        print(f"  Причина: {guess.reason}")

    def cmd_start(self):
        """Запустить сервисы"""
        # Проверяем устройство
        device_error = self.config.get_device_error()
        if device_error:
            print(f"❌ {device_error}")
            print("Настройте устройство: set-device /dev/ttyXXX")
            print("Посмотреть доступные: devices")
            return

        print("🚀 Запуск сервисов...")

        def log(msg):
            print(f"  {msg}")

        if self.docker_manager.start_services(log):
            print("✅ Сервисы запущены!")
            self.cmd_status()
        else:
            print("❌ Ошибка запуска сервисов")

    def cmd_stop(self):
        """Остановить сервисы"""
        print("🛑 Остановка сервисов...")

        def log(msg):
            print(f"  {msg}")

        if self.docker_manager.stop_services(log):
            print("✅ Сервисы остановлены")
        else:
            print("❌ Ошибка остановки")

    def cmd_restart(self):
        """Перезапустить сервисы"""
        # Проверяем устройство
        device_error = self.config.get_device_error()
        if device_error:
            print(f"❌ {device_error}")
            print("Настройте устройство: set-device /dev/ttyXXX")
            return

        print("🔄 Перезапуск сервисов...")

        def log(msg):
            print(f"  {msg}")

        if self.docker_manager.restart_services(log):
            print("✅ Сервисы перезапущены!")
            self.cmd_status()
        else:
            print("❌ Ошибка перезапуска")

    def cmd_down(self):
        """Полная остановка"""
        print("🗑️ Полная остановка сервисов...")

        def log(msg):
            print(f"  {msg}")

        if self.docker_manager.down_services(log):
            print("✅ Контейнеры удалены")
        else:
            print("❌ Ошибка")

    def cmd_logs(self, service=None):
        """Показать логи"""
        print(f"\n📋 Логи {service or 'всех сервисов'}:")
        print("-" * 50)

        logs = self.docker_manager.get_logs_snapshot(service=service, tail=50)
        print(logs)

    def cmd_logs_follow(self, service=None, tail: int = 100):
        """Следить за логами (follow)"""
        print(f"\n📋 Логи -f {service or 'всех сервисов'} (Ctrl+C чтобы выйти):")
        print("-" * 50)

        process = self.docker_manager.get_logs(service=service, tail=tail, follow=True)
        try:
            while True:
                line = process.stdout.readline()
                if line == '' and process.poll() is not None:
                    break
                if line:
                    print(line.rstrip())
        except KeyboardInterrupt:
            pass
        finally:
            try:
                process.terminate()
            except Exception:
                pass

    def cmd_set_device(self, device):
        """Установить Zigbee устройство"""
        self.config.zigbee_device = device
        self.config.save_config()
        print(f"✅ Zigbee устройство: {device}")

    def cmd_set_mqtt_user(self, user):
        """Установить MQTT пользователя"""
        self.config.mqtt_user = user
        self.config.save_config()
        print(f"✅ MQTT пользователь: {user}")

    def cmd_set_mqtt_pass(self, password):
        """Установить MQTT пароль"""
        self.config.mqtt_password = password
        self.config.save_config()
        print("✅ MQTT пароль установлен")

    def cmd_enable_nodered(self):
        """Включить NodeRED"""
        self.config.nodered_enabled = True
        self.config.save_config()
        print("✅ NodeRED включен")

    def cmd_disable_nodered(self):
        """Выключить NodeRED"""
        self.config.nodered_enabled = False
        self.config.save_config()
        print("✅ NodeRED выключен")

    def cmd_enable_cloud(self):
        """Включить облачный MQTT"""
        self.config.cloud_mqtt_enabled = True
        self.config.save_config()
        print("✅ Облачный MQTT включен")

    def cmd_disable_cloud(self):
        """Выключить облачный MQTT"""
        self.config.cloud_mqtt_enabled = False
        self.config.save_config()
        print("✅ Облачный MQTT выключен")

    def cmd_set_cloud_host(self, host):
        """Установить хост облачного MQTT"""
        self.config.cloud_mqtt_host = host
        self.config.save_config()
        print(f"✅ Облачный MQTT хост: {host}")

    def cmd_set_cloud_user(self, user):
        """Установить пользователя облачного MQTT"""
        self.config.cloud_mqtt_user = user
        self.config.save_config()
        print(f"✅ Облачный MQTT пользователь: {user}")

    def cmd_set_cloud_pass(self, password):
        """Установить пароль облачного MQTT"""
        self.config.cloud_mqtt_password = password
        self.config.save_config()
        print("✅ Облачный MQTT пароль установлен")

    def run(self):
        """Запуск интерактивного режима"""
        print("🐝 Z2M Manager")
        print("Введите 'help' для справки")

        while True:
            try:
                command_input = input("\n> ").strip()

                if not command_input:
                    continue

                parts = command_input.split()
                command = parts[0].lower()
                args = parts[1:] if len(parts) > 1 else []

                if command in ['exit', 'quit', 'q']:
                    print("👋 До свидания!")
                    break
                elif command in ['help', 'h']:
                    self.show_help()
                elif command in ['status', 's']:
                    self.cmd_status()
                elif command in ['config', 'c']:
                    self.cmd_config()
                elif command in ['devices', 'd']:
                    self.cmd_devices()
                elif command in ['coordinator', 'coord']:
                    self.cmd_coordinator(args)
                elif command in ['permit-join', 'permit_join', 'permitjoin']:
                    self.cmd_permit_join(args)
                elif command == 'start':
                    self.cmd_start()
                elif command == 'stop':
                    self.cmd_stop()
                elif command == 'restart':
                    self.cmd_restart()
                elif command == 'down':
                    self.cmd_down()
                elif command == 'logs':
                    # поддержка: logs -f [service]
                    if args and args[0] in ("-f", "--follow"):
                        service = args[1] if len(args) > 1 else None
                        self.cmd_logs_follow(service)
                    else:
                        self.cmd_logs(args[0] if args else None)
                elif command in ['ps', 'containers']:
                    self.cmd_status()
                elif command == 'set-device':
                    if args:
                        self.cmd_set_device(args[0])
                    else:
                        print("❌ Укажите устройство: set-device /dev/ttyACM0")
                elif command == 'set-mqtt-user':
                    if args:
                        self.cmd_set_mqtt_user(args[0])
                    else:
                        print("❌ Укажите пользователя: set-mqtt-user user")
                elif command == 'set-mqtt-pass':
                    if args:
                        self.cmd_set_mqtt_pass(args[0])
                    else:
                        print("❌ Укажите пароль: set-mqtt-pass password")
                elif command == 'enable-nodered':
                    self.cmd_enable_nodered()
                elif command == 'disable-nodered':
                    self.cmd_disable_nodered()
                elif command == 'enable-cloud':
                    self.cmd_enable_cloud()
                elif command == 'disable-cloud':
                    self.cmd_disable_cloud()
                elif command == 'set-cloud-host':
                    if args:
                        self.cmd_set_cloud_host(args[0])
                    else:
                        print("❌ Укажите хост: set-cloud-host mq.rocket-home.ru")
                elif command == 'set-cloud-user':
                    if args:
                        self.cmd_set_cloud_user(args[0])
                    else:
                        print("❌ Укажите пользователя: set-cloud-user UUID")
                elif command == 'set-cloud-pass':
                    if args:
                        self.cmd_set_cloud_pass(args[0])
                    else:
                        print("❌ Укажите пароль: set-cloud-pass password")
                else:
                    print(f"❌ Неизвестная команда: {command}")
                    print("Введите 'help' для справки")

            except KeyboardInterrupt:
                print("\n👋 До свидания!")
                break
            except Exception as e:
                print(f"❌ Ошибка: {e}")


def print_usage():
    """Показать краткую справку по командам"""
    print("""
🐝 Z2M Manager - Управление Zigbee2MQTT

Использование: ./z2m [команда] [аргументы]

Команды:
  (без аргументов)    Запустить TUI интерфейс
  --cli               Интерактивный CLI режим
  --wizard            Запустить мастер настройки
  
  start               Запустить сервисы
  stop                Остановить сервисы
  restart             Перезапустить сервисы
  status              Показать статус контейнеров
  ps                  Статус контейнеров (алиас)
  containers          Статус контейнеров (алиас)
  logs [сервис]       Показать логи (mqtt/zigbee2mqtt/nodered)
  logs -f [сервис]    Следить за логами (Ctrl+C чтобы выйти)
  
  config              Показать конфигурацию
  devices             Показать USB устройства
  doctor              Диагностика системы
  coordinator         Определить координатор (ember/zstack) по USB
  coordinator --probe [dev] Активный probe (zstack через serial, silabs через tool)
  permit-join on|off          permit_join в zigbee2mqtt.yaml
  permit-join on|off --mqtt [сек]  runtime permit_join через MQTT (по умолчанию 60 сек)
  
  help, -h, --help    Показать эту справку

Примеры:
  ./z2m               # Запустить TUI
  ./z2m doctor        # Проверить систему
  ./z2m start         # Запустить все сервисы
  ./z2m logs mqtt     # Логи MQTT брокера
  ./z2m status        # Статус контейнеров
""")


def run_quick_command(command: str, args: list) -> int:
    """Выполнить быструю команду и выйти"""
    # Doctor не требует инициализации CLI
    if command == 'doctor':
        checks = run_doctor(verbose=True)
        failed = [c for c in checks if not c.ok]
        return 1 if failed else 0
    
    cli = Z2MCLI()
    
    if command in ('start',):
        cli.cmd_start()
    elif command in ('stop',):
        cli.cmd_stop()
    elif command in ('restart',):
        cli.cmd_restart()
    elif command in ('down',):
        cli.cmd_down()
    elif command in ('status', 's', 'ps', 'containers'):
        cli.cmd_status()
    elif command in ('logs', 'log'):
        # поддержка: logs -f [service]
        if args and args[0] in ("-f", "--follow"):
            service = args[1] if len(args) > 1 else None
            cli.cmd_logs_follow(service)
        else:
            service = args[0] if args else None
            cli.cmd_logs(service)
    elif command in ('config', 'c'):
        cli.cmd_config()
    elif command in ('devices', 'd'):
        cli.cmd_devices()
    elif command in ('coordinator', 'coord'):
        cli.cmd_coordinator(args)
    elif command in ('permit-join', 'permit_join', 'permitjoin'):
        cli.cmd_permit_join(args)
    elif command in ('help', '-h', '--help'):
        print_usage()
    else:
        print(f"❌ Неизвестная команда: {command}")
        print_usage()
        return 1
    
    return 0


# Команды, которые выполняются напрямую (без входа в интерактивный режим)
QUICK_COMMANDS = {
    'start', 'stop', 'restart', 'down',
    'status', 's', 'ps', 'containers',
    'logs', 'log',
    'config', 'c',
    'devices', 'd',
    'doctor',
    'coordinator', 'coord',
    'permit-join', 'permit_join', 'permitjoin',
    'help', '-h', '--help',
}


def main():
    """Точка входа"""
    # Если есть аргументы
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        args = sys.argv[2:]
        
        # Интерактивный CLI режим
        if cmd == "--cli":
            cli = Z2MCLI()
            cli.run()
            return
        
        # Принудительный запуск wizard
        if cmd == "--wizard":
            result = run_wizard()
            if result == "start":
                cli = Z2MCLI()
                cli.cmd_start()
            return
        
        # Быстрые команды
        if cmd in QUICK_COMMANDS:
            sys.exit(run_quick_command(cmd, args))
        
        # Неизвестный аргумент
        print(f"❌ Неизвестная команда: {cmd}")
        print_usage()
        sys.exit(1)
    
    # Проверка первого запуска (wizard)
    wizard_result = maybe_run_wizard()
    if wizard_result == 'exit':
        sys.exit(0)
    elif wizard_result == 'start':
        # Пользователь выбрал запуск после wizard
        cli = Z2MCLI()
        cli.cmd_start()
        input("\nНажмите Enter для запуска TUI...")
    
    # Без аргументов — запуск TUI
    try:
        from .tui import run_tui
        run_tui()
    except ImportError as e:
        print(f"❌ Не удалось загрузить TUI: {e}")
        print("Установите зависимости: pip install -r requirements.txt")
        print("Или запустите в CLI режиме: ./z2m --cli")
        sys.exit(1)


if __name__ == "__main__":
    main()

