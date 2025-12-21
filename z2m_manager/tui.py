"""
TUI интерфейс для управления Z2M окружением
"""
import asyncio
from typing import Optional, List
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Header, Footer, Static, ListView, ListItem, Label,
    Log, Input, Switch, Select, Button
)
from textual.screen import Screen
from textual import on
from textual.binding import Binding

from .config import Z2MConfig
from .docker_manager import DockerManager
from .device_detector import DeviceDetector


class LogsScreen(Screen):
    """Экран просмотра логов"""

    BINDINGS = [
        Binding("escape", "back", "Назад"),
        Binding("r", "refresh", "Обновить"),
        Binding("1", "logs_mqtt", "MQTT"),
        Binding("2", "logs_z2m", "Z2M"),
        Binding("3", "logs_nodered", "NodeRED"),
        Binding("0", "logs_all", "Все"),
    ]

    def __init__(self, service: Optional[str] = None):
        super().__init__()
        self.current_service = service

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            service_name = self.current_service or "все сервисы"
            yield Static(f"📋 Логи: {service_name}", id="logs_title", classes="screen-title")
            yield Log(id="logs_output", auto_scroll=True)
        yield Footer()

    def on_mount(self) -> None:
        self.load_logs()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self.load_logs()

    def action_logs_mqtt(self) -> None:
        self.current_service = "mqtt"
        self._update_title()
        self.load_logs()

    def action_logs_z2m(self) -> None:
        self.current_service = "zigbee2mqtt"
        self._update_title()
        self.load_logs()

    def action_logs_nodered(self) -> None:
        self.current_service = "nodered"
        self._update_title()
        self.load_logs()

    def action_logs_all(self) -> None:
        self.current_service = None
        self._update_title()
        self.load_logs()

    def _update_title(self) -> None:
        title = self.query_one("#logs_title", Static)
        service_name = self.current_service or "все сервисы"
        title.update(f"📋 Логи: {service_name}")

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


class DeviceScreen(Screen):
    """Экран выбора Zigbee устройства"""

    BINDINGS = [Binding("escape", "back", "Назад")]

    def compose(self) -> ComposeResult:
        yield Header()
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
                yield Select(
                    options=[],
                    id="zigbee_device",
                    allow_blank=True,
                )

            yield Button("🔍 Обновить список", id="refresh_devices", variant="default")
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
        else:
            self.app.notify("⚠️ Выберите устройство", severity="warning")

    @on(Button.Pressed, "#cancel_btn")
    def on_cancel(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#refresh_devices")
    def on_refresh(self) -> None:
        select = self.query_one("#zigbee_device", Select)
        select.set_options(self._get_device_options())
        self.app.notify("🔍 Список обновлён")

    def action_back(self) -> None:
        self.app.pop_screen()


class CloudMqttScreen(Screen):
    """Экран настройки облачного MQTT"""

    BINDINGS = [Binding("escape", "back", "Назад")]

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static("☁️ Облачный MQTT", classes="screen-title")

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

    @on(Button.Pressed, "#cancel_btn")
    def on_cancel(self) -> None:
        self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()


class NodeRedScreen(Screen):
    """Экран настройки NodeRED"""

    BINDINGS = [Binding("escape", "back", "Назад")]

    def compose(self) -> ComposeResult:
        yield Header()
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

    @on(Button.Pressed, "#cancel_btn")
    def on_cancel(self) -> None:
        self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()


class SettingsScreen(Screen):
    """Экран настроек (подменю)"""

    BINDINGS = [Binding("escape", "back", "Назад")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            yield Static("⚙️ Настройки", classes="screen-title")
            with ListView(id="settings_menu"):
                yield ListItem(Label("🔌 Z2M устройство"), id="menu_device")
                yield ListItem(Label("☁️ Облачный MQTT"), id="menu_cloud")
                yield ListItem(Label("🔴 NodeRED"), id="menu_nodered")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#settings_menu", ListView).focus()
        self.query_one("#settings_menu", ListView).index = 0

    @on(ListView.Selected)
    def on_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id == "menu_device":
            self.app.push_screen(DeviceScreen())
        elif item_id == "menu_cloud":
            self.app.push_screen(CloudMqttScreen())
        elif item_id == "menu_nodered":
            self.app.push_screen(NodeRedScreen())

    def action_back(self) -> None:
        self.app.pop_screen()


class ControlScreen(Screen):
    """Экран управления (подменю)"""

    BINDINGS = [Binding("escape", "back", "Назад")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            yield Static("🐳 Управление", classes="screen-title")
            with ListView(id="control_menu"):
                yield ListItem(Label("🚀 Запустить"), id="menu_start")
                yield ListItem(Label("🛑 Остановить"), id="menu_stop")
                yield ListItem(Label("🔄 Перезапустить"), id="menu_restart")
                yield ListItem(Label("📋 Логи"), id="menu_logs")
                yield ListItem(Label("🗑️ Удалить контейнеры"), id="menu_down")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#control_menu", ListView).focus()
        self.query_one("#control_menu", ListView).index = 0

    @on(ListView.Selected)
    async def on_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id

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
            await self.app.run_docker_operation("🗑️ Удаление контейнеров", self.app._do_down)

    def action_back(self) -> None:
        self.app.pop_screen()


class Z2MApp(App):
    """Основное TUI приложение для управления Z2M"""

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
        Binding("s", "start", "▶ Запустить"),
        Binding("x", "stop", "■ Стоп"),
        Binding("r", "restart", "↻ Рестарт"),
        Binding("l", "logs", "📋 Логи"),
        Binding("c", "settings", "⚙ Настройки"),
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
        yield Header()
        with Container():
            yield Static("🐝 Zigbee2MQTT Manager", classes="screen-title")

            # Статус - текстовый блок без фокуса
            yield Static(id="status_panel")

            # Главное меню
            with ListView(id="main_menu"):
                yield ListItem(Label("⚙️ Настройки"), id="menu_settings")
                yield ListItem(Label("🐳 Управление"), id="menu_control")

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

    @on(ListView.Selected, "#main_menu")
    def on_main_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id == "menu_settings":
            self.push_screen(SettingsScreen())
        elif item_id == "menu_control":
            self.push_screen(ControlScreen())

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

    async def action_start(self) -> None:
        """Горячая клавиша: Запустить сервисы"""
        device_error = self.config.get_device_error()
        if device_error:
            self.notify(f"⚠️ {device_error}", severity="error")
            self.push_screen(DeviceScreen())
            return
        await self.run_docker_operation("🚀 Запуск сервисов", self._do_start)

    async def action_stop(self) -> None:
        """Горячая клавиша: Остановить сервисы"""
        await self.run_docker_operation("🛑 Остановка сервисов", self._do_stop)

    async def action_restart(self) -> None:
        """Горячая клавиша: Перезапустить сервисы"""
        device_error = self.config.get_device_error()
        if device_error:
            self.notify(f"⚠️ {device_error}", severity="error")
            self.push_screen(DeviceScreen())
            return
        await self.run_docker_operation("🔄 Перезапуск сервисов", self._do_restart)

    def action_logs(self) -> None:
        """Горячая клавиша: Открыть логи"""
        self.push_screen(LogsScreen())

    def action_settings(self) -> None:
        """Горячая клавиша: Открыть настройки"""
        self.push_screen(SettingsScreen())


def run_tui():
    """Запуск TUI приложения"""
    app = Z2MApp()
    app.run()
