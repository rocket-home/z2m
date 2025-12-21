"""
TUI интерфейс для управления Z2M окружением
"""
import asyncio
import os
import getpass
import grp
from pathlib import Path
from typing import Optional, List
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Header, Footer, Static, ListView, ListItem, Label,
    Log, Input, Switch, Select, Button
)
from textual.screen import Screen
from textual import on, events
from textual.binding import Binding

from .config import Z2MConfig
from .docker_manager import DockerManager
from .device_detector import DeviceDetector


class ArrowNavScreen(Screen):
    """Навигация по элементам формы стрелками ↑/↓ (без ломания меню/Select/логов)."""

    _ARROW_NAV_SKIP = (ListView, Select, Log)

    def on_key(self, event: events.Key) -> None:
        focused = getattr(self.app, "focused", None)
        if focused is not None and isinstance(focused, self._ARROW_NAV_SKIP):
            return

        # Внутри текстовых полей не перехватываем ←/→, чтобы не ломать перемещение курсора
        if focused is not None and isinstance(focused, Input):
            if event.key == "down":
                try:
                    self.app.action_focus_next()
                    event.stop()
                except Exception:
                    return
            elif event.key == "up":
                try:
                    self.app.action_focus_previous()
                    event.stop()
                except Exception:
                    return
            return

        if event.key in ("right", "down"):
            try:
                self.app.action_focus_next()
                event.stop()
            except Exception:
                return
        elif event.key in ("left", "up"):
            try:
                self.app.action_focus_previous()
                event.stop()
            except Exception:
                return


class LogsScreen(Screen):
    """Экран просмотра логов"""

    BINDINGS = [
        Binding("escape", "back", "Назад"),
        Binding("r", "refresh", "Обновить"),
        Binding("f", "toggle_follow", "Follow"),
        Binding("1", "logs_mqtt", "MQTT"),
        Binding("2", "logs_z2m", "Z2M"),
        Binding("3", "logs_nodered", "NodeRED"),
        Binding("0", "logs_all", "Все"),
    ]

    def __init__(self, service: Optional[str] = None, follow: bool = True):
        super().__init__()
        self.current_service = service
        self.follow = follow
        self._follow_task: Optional[asyncio.Task] = None
        self._follow_process = None

    def compose(self) -> ComposeResult:
        with Container():
            service_name = self.current_service or "все сервисы"
            yield Static(f"📋 Логи: {service_name}", id="logs_title", classes="screen-title")
            yield Log(id="logs_output", auto_scroll=True)
        yield Footer()

    def on_mount(self) -> None:
        self._update_title()
        if self.follow:
            self.start_follow()
        else:
            self.load_logs()

    def action_back(self) -> None:
        self.stop_follow()
        self.app.pop_screen()

    def action_refresh(self) -> None:
        if self.follow:
            # В follow-режиме refresh перезапускает поток
            self.start_follow(restart=True)
        else:
            self.load_logs()

    def action_toggle_follow(self) -> None:
        self.follow = not self.follow
        self._update_title()
        if self.follow:
            self.start_follow(restart=True)
        else:
            self.stop_follow()
            self.load_logs()

    def action_logs_mqtt(self) -> None:
        self.current_service = "mqtt"
        self._update_title()
        if self.follow:
            self.start_follow(restart=True)
        else:
            self.load_logs()

    def action_logs_z2m(self) -> None:
        self.current_service = "zigbee2mqtt"
        self._update_title()
        if self.follow:
            self.start_follow(restart=True)
        else:
            self.load_logs()

    def action_logs_nodered(self) -> None:
        self.current_service = "nodered"
        self._update_title()
        if self.follow:
            self.start_follow(restart=True)
        else:
            self.load_logs()

    def action_logs_all(self) -> None:
        self.current_service = None
        self._update_title()
        if self.follow:
            self.start_follow(restart=True)
        else:
            self.load_logs()

    def _update_title(self) -> None:
        title = self.query_one("#logs_title", Static)
        service_name = self.current_service or "все сервисы"
        mode = "follow" if self.follow else "snapshot"
        title.update(f"📋 Логи ({mode}): {service_name}")

    def load_logs(self) -> None:
        log_widget = self.query_one("#logs_output", Log)
        log_widget.clear()

        if not hasattr(self.app, 'docker_manager'):
            log_widget.write_line("❌ Docker manager не инициализирован")
            return

        logs = self.app.docker_manager.get_logs_snapshot(
            service=self.current_service,
            tail=100
        )

        for line in logs.split('\n'):
            if line.strip():
                log_widget.write_line(line)

    def stop_follow(self) -> None:
        """Остановить follow-процесс и таску."""
        if self._follow_task is not None:
            self._follow_task.cancel()
            self._follow_task = None
        if self._follow_process is not None:
            try:
                self._follow_process.terminate()
            except Exception:
                pass
            self._follow_process = None

    def start_follow(self, restart: bool = False) -> None:
        """Запустить стрим логов docker-compose logs -f."""
        if not hasattr(self.app, 'docker_manager'):
            log_widget = self.query_one("#logs_output", Log)
            log_widget.clear()
            log_widget.write_line("❌ Docker manager не инициализирован")
            return

        if self._follow_task is not None or self._follow_process is not None:
            if not restart:
                return
            self.stop_follow()

        log_widget = self.query_one("#logs_output", Log)
        log_widget.clear()
        log_widget.write_line("⏳ Подключаюсь к логам... (f — переключить режим)")

        self._follow_process = self.app.docker_manager.get_logs(
            service=self.current_service,
            tail=100,
            follow=True,
        )

        async def _reader() -> None:
            assert self._follow_process is not None
            proc = self._follow_process
            # Читаем блокирующие readline в отдельном потоке
            while True:
                line = await asyncio.to_thread(proc.stdout.readline)
                if line == '' and proc.poll() is not None:
                    break
                if line:
                    log_widget.write_line(line.rstrip("\n"))

        self._follow_task = asyncio.create_task(_reader())


class DeviceScreen(ArrowNavScreen):
    """Экран выбора Zigbee устройства"""

    BINDINGS = [
        Binding("escape", "back", "Назад"),
        Binding("f10", "save_and_exit", "Сохранить и выйти"),
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("🔌 Zigbee USB адаптер", classes="screen-title")

            options = self._get_device_options()
            if options:
                yield Static("Выберите устройство из списка:", classes="config-hint")
                yield Select(
                    options=options,
                    id="zigbee_device",
                    allow_blank=True,
                )
            else:
                yield Static("⚠️ USB устройства не обнаружены", classes="config-warning")
                yield Static("Подключите Zigbee адаптер и нажмите 'Обновить'", classes="config-hint")
                yield Static("Если адаптер подключен, но не виден — откройте: Настройки → Доступ к USB", classes="config-hint")
                yield Select(
                    options=[],
                    id="zigbee_device",
                    allow_blank=True,
                )

            yield Button("🔍 Обновить список", id="refresh_devices", variant="default")
            yield Button("🔐 Доступ к USB (инструкция)", id="usb_access_help", variant="default")
            yield Static("", classes="spacer")
            with Horizontal(classes="button-row"):
                yield Button("💾 Сохранить", id="save_btn", variant="primary")
                yield Button("❌ Отмена", id="cancel_btn", variant="error")
        yield Footer()

    def _get_device_options(self) -> List[tuple]:
        devices = DeviceDetector.detect_serial_devices()
        options = []

        for device in devices:
            path = device.get('by_id', device['path'])
            desc = device.get('description', 'Unknown')
            is_zigbee = device.get('is_zigbee', False)
            label = f"{'⚡' if is_zigbee else '📟'} {path} - {desc}"
            options.append((label, path))

        return options

    def on_mount(self) -> None:
        try:
            select = self.query_one("#zigbee_device", Select)
            select.value = self.app.config.zigbee_device
            select.focus()
        except Exception:
            pass

    @on(Button.Pressed, "#save_btn")
    def on_save(self) -> None:
        select = self.query_one("#zigbee_device", Select)
        if select.value and select.value != Select.BLANK:
            self.app.config.zigbee_device = select.value
            self.app.config.save_config()
            self.app.notify("✅ Устройство сохранено")
            self.app.refresh_status()
            self.app.pop_screen()
            self.app.prompt_restart_if_running()
        else:
            self.app.notify("⚠️ Выберите устройство", severity="warning")

    def action_save_and_exit(self) -> None:
        self.on_save()

    @on(Button.Pressed, "#cancel_btn")
    def on_cancel(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#refresh_devices")
    def on_refresh(self) -> None:
        select = self.query_one("#zigbee_device", Select)
        select.set_options(self._get_device_options())
        self.app.notify("🔍 Список обновлён")

    @on(Button.Pressed, "#usb_access_help")
    def on_usb_access_help(self) -> None:
        self.app.push_screen(UsbAccessScreen())

    def action_back(self) -> None:
        self.app.pop_screen()


class CloudMqttScreen(ArrowNavScreen):
    """Экран настройки облачного MQTT"""

    BINDINGS = [
        Binding("escape", "back", "Назад"),
        Binding("f10", "save_and_exit", "Сохранить и выйти"),
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("☁️ Облачный MQTT", classes="screen-title")

            yield Static("🔗 Профиль MQTT RocketHome: https://rocket-home.ru/profile/mqtt", classes="config-hint")

            with Horizontal(classes="switch-row"):
                yield Static("Включить бридж:", classes="config-label-inline")
                yield Switch(id="cloud_enabled")

            yield Static("", classes="spacer")

            yield Static("Хост:", classes="config-label")
            yield Input(id="cloud_host", placeholder="mq.rocket-home.ru")

            yield Static("Пользователь (UUID):", classes="config-label")
            yield Input(id="cloud_user", placeholder="XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX")

            yield Static("Пароль:", classes="config-label")
            yield Input(id="cloud_password", placeholder="password", password=True)

            yield Static("", classes="spacer")

            with Horizontal(classes="button-row"):
                yield Button("💾 Сохранить", id="save_btn", variant="primary")
                yield Button("❌ Отмена", id="cancel_btn", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        config = self.app.config
        switch = self.query_one("#cloud_enabled", Switch)
        switch.value = config.cloud_mqtt_enabled
        switch.focus()
        self.query_one("#cloud_host", Input).value = config.cloud_mqtt_host
        self.query_one("#cloud_user", Input).value = config.cloud_mqtt_user
        self.query_one("#cloud_password", Input).value = config.cloud_mqtt_password

    @on(Button.Pressed, "#save_btn")
    def on_save(self) -> None:
        config = self.app.config
        config.cloud_mqtt_enabled = self.query_one("#cloud_enabled", Switch).value
        config.cloud_mqtt_host = self.query_one("#cloud_host", Input).value
        config.cloud_mqtt_user = self.query_one("#cloud_user", Input).value
        config.cloud_mqtt_password = self.query_one("#cloud_password", Input).value
        config.save_config()
        self.app.notify("✅ Настройки сохранены")
        self.app.refresh_status()
        self.app.pop_screen()
        self.app.prompt_restart_if_running()

    def action_save_and_exit(self) -> None:
        self.on_save()

    @on(Button.Pressed, "#cancel_btn")
    def on_cancel(self) -> None:
        self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()


class NodeRedScreen(ArrowNavScreen):
    """Экран настройки NodeRED"""

    BINDINGS = [
        Binding("escape", "back", "Назад"),
        Binding("f10", "save_and_exit", "Сохранить и выйти"),
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("🔴 NodeRED", classes="screen-title")

            with Horizontal(classes="switch-row"):
                yield Static("Включить NodeRED:", classes="config-label-inline")
                yield Switch(id="nodered_enabled")

            yield Static("", classes="spacer")
            yield Static("NodeRED будет доступен на порту 1880", classes="config-hint")

            yield Static("", classes="spacer")

            with Horizontal(classes="button-row"):
                yield Button("💾 Сохранить", id="save_btn", variant="primary")
                yield Button("❌ Отмена", id="cancel_btn", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        switch = self.query_one("#nodered_enabled", Switch)
        switch.value = self.app.config.nodered_enabled
        switch.focus()

    @on(Button.Pressed, "#save_btn")
    def on_save(self) -> None:
        self.app.config.nodered_enabled = self.query_one("#nodered_enabled", Switch).value
        self.app.config.save_config()
        self.app.notify("✅ Настройки сохранены")
        self.app.refresh_status()
        self.app.pop_screen()
        self.app.prompt_restart_if_running()

    def action_save_and_exit(self) -> None:
        self.on_save()

    @on(Button.Pressed, "#cancel_btn")
    def on_cancel(self) -> None:
        self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()


# DevicesFileScreen удалён: теперь это единственный режим (devices всегда в отдельном файле).


class UsbAccessScreen(ArrowNavScreen):
    """Экран настройки доступа к USB"""

    BINDINGS = [Binding("escape", "back", "Назад")]

    def _project_root(self) -> Path:
        # z2m_manager/ -> project root
        return Path(__file__).parent.parent

    def _rules_src(self) -> Path:
        return self._project_root() / "99-zigbee.rules"

    def _rules_dst(self) -> Path:
        return Path("/etc/udev/rules.d/99-zigbee.rules")

    def _user_in_group(self, group: str) -> bool:
        user = getpass.getuser()
        try:
            gid = grp.getgrnam(group).gr_gid
        except KeyError:
            return False
        gids = os.getgroups()
        if gid in gids:
            return True
        # На всякий случай: проверим primary group
        return os.getgid() == gid

    def _refresh_status(self) -> None:
        panel = self.query_one("#usb_status", Static)
        in_dialout = self._user_in_group("dialout")
        rules_installed = self._rules_dst().exists()

        devices = []
        for p in ("/dev/ttyUSB0", "/dev/ttyACM0", "/dev/zigbee"):
            if Path(p).exists():
                devices.append(p)

        lines = [
            f"[b]dialout:[/b] {'✅' if in_dialout else '❌'}",
            f"[b]udev rules:[/b] {'✅' if rules_installed else '❌'} ({self._rules_dst()})",
            f"[b]/dev nodes:[/b] {', '.join(devices) if devices else 'не найдены'}",
        ]
        panel.update("\n".join(lines))

    def _run_in_terminal(self, title: str, command: str) -> None:
        """Выполнить команду в реальном терминале (для sudo)."""
        with self.app.suspend():
            print(f"\n{'='*60}\n{title}\n{'='*60}\n")
            # Важно: используем /bin/bash для редиректов/глобов
            os.system(f"/bin/bash -lc {command!r}")
            input("\nНажмите Enter для возврата в TUI...")
        self._refresh_status()

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("🔐 Доступ к USB (Zigbee адаптер)", classes="screen-title")
            yield Static(id="usb_status")
            yield Static("Действия требуют sudo. После добавления в dialout может понадобиться перелогиниться.", classes="config-hint")

            with Horizontal(classes="button-row"):
                yield Button("➕ dialout", id="usb_add_dialout", variant="primary")
                yield Button("📄 udev правила", id="usb_install_rules", variant="primary")
            with Horizontal(classes="button-row"):
                yield Button("🔄 reload udev", id="usb_reload_udev", variant="default")
                yield Button("🔎 проверить /dev", id="usb_check_dev", variant="default")
            with Horizontal(classes="button-row"):
                yield Button("▶ выполнить всё", id="usb_run_all", variant="success")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_status()
        try:
            self.query_one("#usb_add_dialout", Button).focus()
        except Exception:
            pass

    @on(Button.Pressed, "#usb_add_dialout")
    def on_add_dialout(self) -> None:
        user = getpass.getuser()
        self._run_in_terminal(
            "Добавление пользователя в группу dialout",
            f"sudo usermod -aG dialout {user} && echo && echo 'Готово. Перелогиньтесь или выполните: newgrp dialout'"
        )

    @on(Button.Pressed, "#usb_install_rules")
    def on_install_rules(self) -> None:
        src = self._rules_src()
        if not src.exists():
            self.app.notify(f"❌ Не найден файл правил: {src}", severity="error")
            return
        self._run_in_terminal(
            "Установка udev-правил для Zigbee адаптера",
            f"sudo cp {str(src)!r} /etc/udev/rules.d/99-zigbee.rules && sudo udevadm control --reload-rules && sudo udevadm trigger"
        )

    @on(Button.Pressed, "#usb_reload_udev")
    def on_reload_udev(self) -> None:
        self._run_in_terminal(
            "Перезагрузка udev правил",
            "sudo udevadm control --reload-rules && sudo udevadm trigger"
        )

    @on(Button.Pressed, "#usb_check_dev")
    def on_check_dev(self) -> None:
        self._run_in_terminal(
            "Проверка устройств",
            "ls -la /dev/ttyUSB* /dev/ttyACM* /dev/zigbee 2>/dev/null || true"
        )

    @on(Button.Pressed, "#usb_run_all")
    def on_run_all(self) -> None:
        user = getpass.getuser()
        src = self._rules_src()
        if not src.exists():
            self.app.notify(f"❌ Не найден файл правил: {src}", severity="error")
            return
        self._run_in_terminal(
            "Настройка доступа к USB (всё сразу)",
            "set -euo pipefail; "
            f"sudo usermod -aG dialout {user}; "
            f"sudo cp {str(src)!r} /etc/udev/rules.d/99-zigbee.rules; "
            "sudo udevadm control --reload-rules; "
            "sudo udevadm trigger; "
            "echo; "
            "ls -la /dev/ttyUSB* /dev/ttyACM* /dev/zigbee 2>/dev/null || true; "
            "echo; "
            "echo 'Если dialout был добавлен только что — перелогиньтесь или выполните: newgrp dialout'"
        )

    def action_back(self) -> None:
        self.app.pop_screen()


class SettingsScreen(Screen):
    """Экран настроек (подменю)"""

    BINDINGS = [Binding("escape", "back", "Назад")]

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("⚙️ Настройки", classes="screen-title")
            with ListView(id="settings_menu"):
                yield ListItem(Label("🔐 Доступ к USB"), id="menu_usb_access")
                yield ListItem(Label("🔌 Z2M устройство"), id="menu_device")
                yield ListItem(Label("☁️ Облачный MQTT"), id="menu_cloud")
                yield ListItem(Label("🔴 NodeRED"), id="menu_nodered")
                yield ListItem(Label("↩ Назад"), id="menu_back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#settings_menu", ListView).focus()
        self.query_one("#settings_menu", ListView).index = 0

    @on(ListView.Selected)
    def on_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id == "menu_device":
            self.app.push_screen(DeviceScreen())
        elif item_id == "menu_usb_access":
            self.app.push_screen(UsbAccessScreen())
        elif item_id == "menu_cloud":
            self.app.push_screen(CloudMqttScreen())
        elif item_id == "menu_nodered":
            self.app.push_screen(NodeRedScreen())
        elif item_id == "menu_back":
            self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()


class ControlScreen(Screen):
    """Экран управления (подменю)"""

    BINDINGS = [Binding("escape", "back", "Назад")]

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("🐳 Управление", classes="screen-title")
            with ListView(id="control_menu"):
                yield ListItem(Label("📊 Статус"), id="menu_status")
                yield ListItem(Label("🚀 Запустить"), id="menu_start")
                yield ListItem(Label("🛑 Остановить"), id="menu_stop")
                yield ListItem(Label("🔄 Перезапустить"), id="menu_restart")
                yield ListItem(Label("📋 Логи"), id="menu_logs")
                yield ListItem(Label("🗑️ Удалить контейнеры"), id="menu_down")
                yield ListItem(Label("↩ Назад"), id="menu_back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#control_menu", ListView).focus()
        self.query_one("#control_menu", ListView).index = 0

    @on(ListView.Selected)
    async def on_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id

        if item_id == "menu_back":
            self.app.pop_screen()
            return
        if item_id == "menu_status":
            self.app.push_screen(StatusScreen())
            return

        # Проверяем устройство перед запуском/перезапуском
        if item_id in ("menu_start", "menu_restart"):
            device_error = self.app.config.get_device_error()
            if device_error:
                self.app.notify(f"⚠️ {device_error}", severity="error")
                self.app.push_screen(DeviceScreen())
                return

        if item_id == "menu_start":
            await self.app.run_docker_operation("🚀 Запуск сервисов", self.app._do_start)
        elif item_id == "menu_stop":
            await self.app.run_docker_operation("🛑 Остановка сервисов", self.app._do_stop)
        elif item_id == "menu_restart":
            await self.app.run_docker_operation("🔄 Перезапуск сервисов", self.app._do_restart)
        elif item_id == "menu_logs":
            self.app.push_screen(LogsScreen())
        elif item_id == "menu_down":
            self.app.push_screen(ConfirmDownScreen())

    def action_back(self) -> None:
        self.app.pop_screen()


class StatusScreen(Screen):
    """Экран просмотра статуса контейнеров (docker-compose ps)."""

    BINDINGS = [
        Binding("escape", "back", "Назад"),
        Binding("r", "refresh", "Обновить"),
    ]

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("📊 Статус контейнеров", classes="screen-title")
            yield Log(id="status_output", auto_scroll=False)
        yield Footer()

    def on_mount(self) -> None:
        self.load_status()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self.load_status()

    def load_status(self) -> None:
        log_widget = self.query_one("#status_output", Log)
        log_widget.clear()

        status = self.app.docker_manager.get_container_status()
        if not status:
            log_widget.write_line("(контейнеры не запущены)")
            return

        for service, info in status.items():
            overall = info.get("overall", "unknown")
            log_widget.write_line(f"{service}: {overall}")


class ConfirmDownScreen(ArrowNavScreen):
    """Подтверждение удаления контейнеров (docker-compose down)."""

    BINDINGS = [Binding("escape", "back", "Отмена")]

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("⚠️ Удалить контейнеры?", classes="screen-title")
            yield Static(
                "Это выполнит docker-compose down.\n"
                "Контейнеры будут удалены (тома сохранятся, если не удалять их отдельно).",
                classes="config-hint",
            )
            with Horizontal(classes="button-row"):
                yield Button("🗑️ Удалить", id="confirm_down_yes", variant="error")
                yield Button("❌ Отмена", id="confirm_down_no", variant="default")
        yield Footer()

    def on_mount(self) -> None:
        try:
            self.query_one("#confirm_down_yes", Button).focus()
        except Exception:
            pass

    @on(Button.Pressed, "#confirm_down_yes")
    async def on_yes(self) -> None:
        self.app.pop_screen()
        await self.app.run_docker_operation("🗑️ Удаление контейнеров", self.app._do_down)

    @on(Button.Pressed, "#confirm_down_no")
    def on_no(self) -> None:
        self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()


class Z2MApp(App):
    """Основное TUI приложение для управления Z2M"""

    # Отключаем Command Palette (palette)
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen {
        background: $background;
    }

    .screen-title {
        text-align: center;
        text-style: bold;
        margin: 1 0;
        color: $primary;
        background: $primary 10%;
        padding: 1;
    }

    .config-hint {
        color: $text-muted;
        margin: 1 0;
        text-style: italic;
    }

    .config-warning {
        color: $warning;
        margin: 1 0;
        text-style: bold;
    }

    .config-label {
        margin-top: 1;
        color: $text-muted;
    }

    .config-label-inline {
        width: auto;
        margin-right: 1;
    }

    .code-block {
        margin: 1 0;
        padding: 1 2;
        background: $surface;
        border: solid $primary-darken-2;
    }

    .spacer {
        height: 1;
    }

    .switch-row {
        height: 3;
        align: left middle;
    }

    .button-row {
        margin-top: 2;
        height: 3;
        align: center middle;
    }

    .button-row Button {
        margin: 0 1;
    }

    #status_panel {
        margin: 1 2;
        padding: 1;
        background: $surface;
        border: solid $primary-darken-2;
        height: auto;
    }

    .status-line {
        margin: 0;
        padding: 0;
    }

    ListView {
        margin: 1 2;
        background: $panel;
        border: solid $primary-darken-3;
        height: auto;
    }

    ListItem {
        margin: 0;
        padding: 0 2;
    }

    ListItem:hover {
        background: $primary 20%;
    }

    Log {
        margin: 1 2;
        border: solid $primary;
        background: $surface;
    }

    Input {
        margin: 0 0 1 0;
    }

    Select {
        margin: 0 0 1 0;
    }

    VerticalScroll {
        margin: 1;
        padding: 0 2;
    }

    Container {
        margin: 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Выход"),
        Binding("escape", "quit", "Выход"),
    ]

    def __init__(self):
        super().__init__()
        try:
            self.config = Z2MConfig()
            self.docker_manager = DockerManager(self.config)
        except Exception as e:
            print(f"❌ Ошибка инициализации: {e}")
            raise

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("🐝 Zigbee2MQTT Manager", classes="screen-title")

            # Статус - текстовый блок без фокуса
            yield Static(id="status_panel")

            # Главное меню
            with ListView(id="main_menu"):
                yield ListItem(Label("⚙️ Настройки"), id="menu_settings")
                yield ListItem(Label("🐳 Управление"), id="menu_control")
                yield ListItem(Label("🚪 Выход"), id="menu_exit")

        yield Footer()

    def on_mount(self) -> None:
        self.refresh_status()
        # Автообновление статуса каждые 5 секунд
        self.set_interval(5, self.refresh_status)

    def refresh_status(self) -> None:
        """Обновление статуса"""
        panel = self.query_one("#status_panel", Static)

        # Собираем статус
        is_running = self.docker_manager.is_running()
        status_icon = "✅ Запущено" if is_running else "⏹️ Остановлено"

        config = self.config

        # Проверяем устройство
        device_error = config.get_device_error()
        if device_error:
            device_str = f"[red]⚠️ {device_error}[/red]"
        else:
            device_str = config.zigbee_device

        cloud = "✅ Вкл" if config.cloud_mqtt_enabled else "❌ Выкл"
        cloud_host = config.cloud_mqtt_host if config.cloud_mqtt_enabled else ""
        nodered = "✅ Вкл" if config.nodered_enabled else "❌ Выкл"

        lines = [
            f"[b]Статус:[/b] {status_icon}",
            f"[b]Устройство:[/b] {device_str}",
            f"[b]Cloud MQTT:[/b] {cloud} {cloud_host}",
            f"[b]NodeRED:[/b] {nodered}",
        ]

        panel.update("\n".join(lines))

    def prompt_restart_if_running(self) -> None:
        """Если контейнеры запущены — предложить перезапуск после изменения настроек."""
        try:
            if self.docker_manager.is_running():
                self.push_screen(RestartPromptScreen())
        except Exception:
            # Ничего критичного — просто не показываем prompt
            return

    @on(ListView.Selected, "#main_menu")
    def on_main_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id == "menu_settings":
            self.push_screen(SettingsScreen())
        elif item_id == "menu_control":
            self.push_screen(ControlScreen())
        elif item_id == "menu_exit":
            self.exit()

    async def run_docker_operation(self, title: str, operation) -> None:
        """Запуск операции с выводом в терминал"""
        try:
            self.notify(f"Переход в терминал: {title}")
            await asyncio.sleep(0.3)

            with self.suspend():
                def log_to_terminal(msg):
                    print(msg)

                print(f"\n{'='*50}")
                print(f" {title}")
                print(f"{'='*50}\n")

                success = await asyncio.to_thread(operation, log_to_terminal)

                if success:
                    print(f"\n✅ Операция завершена успешно")
                else:
                    print(f"\n❌ Операция завершилась с ошибкой")

                input("\nНажмите Enter для возврата в меню...")

            self.refresh_status()

            if success:
                self.notify(f"✅ {title} завершено")
            else:
                self.notify(f"❌ {title} завершено с ошибкой", severity="error")

        except Exception as e:
            with self.suspend():
                print(f"\n❌ Критическая ошибка: {e}")
                input("\nНажмите Enter для возврата в меню...")
            self.notify(f"❌ Ошибка: {e}", severity="error")

    def _do_start(self, log_callback) -> bool:
        return self.docker_manager.start_services(log_callback)

    def _do_stop(self, log_callback) -> bool:
        return self.docker_manager.stop_services(log_callback)

    def _do_restart(self, log_callback) -> bool:
        return self.docker_manager.restart_services(log_callback)

    def _do_down(self, log_callback) -> bool:
        return self.docker_manager.down_services(log_callback)

    def action_quit(self) -> None:
        self.exit()


class RestartPromptScreen(ArrowNavScreen):
    """Диалог предложения перезапустить контейнеры после изменения конфигурации."""

    BINDINGS = [Binding("escape", "back", "Назад")]

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("♻️ Настройки изменены", classes="screen-title")
            yield Static(
                "Чтобы изменения применились, обычно требуется перезапустить контейнеры.\nПерезапустить сейчас?",
                classes="config-hint",
            )
            with Horizontal(classes="button-row"):
                yield Button("🔄 Перезапустить", id="restart_now", variant="primary")
                yield Button("Позже", id="restart_later", variant="default")
        yield Footer()

    @on(Button.Pressed, "#restart_now")
    async def on_restart_now(self) -> None:
        self.app.pop_screen()
        await self.app.run_docker_operation("🔄 Перезапуск сервисов", self.app._do_restart)

    @on(Button.Pressed, "#restart_later")
    def on_restart_later(self) -> None:
        self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()


def run_tui():
    """Запуск TUI приложения"""
    app = Z2MApp()
    app.run()
