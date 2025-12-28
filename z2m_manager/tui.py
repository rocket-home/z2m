"""
TUI интерфейс для управления Z2M окружением
"""
import asyncio
import os
import shutil
import shlex
import getpass
import grp
from pathlib import Path
from typing import Optional, List, Callable
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
from .coordinator_detector import guess_driver_from_device_info, probe_coordinator, install_universal_silabs_flasher
from .mqtt_test import set_z2m_permit_join


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
        with Container(id="device_screen_root"):
            yield Static("🔌 Zigbee USB адаптер", id="device_title", classes="screen-title")
            # Нормальные, читаемые строки (место есть)
            yield Static("", id="device_adapter_line")
            yield Static("", id="device_link_line", classes="config-hint")
            yield Static("", id="device_coord_line", classes="config-hint")

            with ListView(id="device_actions"):
                yield ListItem(Label("🔌 Выбрать устройство"), id="act_pick_device")
                yield ListItem(Label("🔗 Создать линк /dev/zigbee → выбранное устройство"), id="act_make_link")
                yield ListItem(Label("🔁 Использовать /dev/zigbee: ВЫКЛ"), id="act_toggle_link")
                yield ListItem(Label("🧪 Probe драйвера (zstack/ember)"), id="act_probe")
                yield ListItem(Label("💾 Сохранить"), id="act_save")
                yield ListItem(Label("↩ Назад"), id="act_back")
        yield Footer()

    def _run_in_terminal(self, title: str, command: str) -> None:
        """Выполнить команду в реальном терминале (для sudo)."""
        with self.app.suspend():
            print(f"\n{'='*60}\n{title}\n{'='*60}\n")
            os.system("/bin/bash -lc " + shlex.quote(command))
            input("\nНажмите Enter для возврата в TUI...")

    def _get_device_options(self) -> List[tuple]:
        devices = DeviceDetector.detect_serial_devices()
        options = []
        # value -> device_info
        self._device_map = {}

        for device in devices:
            # В селекте показываем физические устройства/стабильные by-id, но НЕ /dev/zigbee
            by_id = device.get("by_id")
            if by_id and by_id != "/dev/zigbee":
                display = by_id
            else:
                display = device["path"]
            desc = device.get('description', 'Unknown')
            is_zigbee = device.get('is_zigbee', False)
            if display != device["path"]:
                label = f"{'⚡' if is_zigbee else '📟'} {display} → {device['path']} - {desc}"
            else:
                label = f"{'⚡' if is_zigbee else '📟'} {display} - {desc}"
            options.append((label, display))
            self._device_map[display] = device
            # также сохраняем по реальному пути
            self._device_map[device.get("path", display)] = device

        return options

    def _get_selected_value(self) -> Optional[str]:
        return getattr(self, "_selected_device", None)

    def _set_selected_device(self, value: Optional[str]) -> None:
        self._selected_device = value

    def _set_use_link(self, use_link: bool) -> None:
        self._use_link = bool(use_link)
        try:
            item = self.query_one("#act_toggle_link", ListItem)
            label = item.query_one(Label)
            label.update(f"🔁 Использовать /dev/zigbee: {'ВКЛ' if self._use_link else 'ВЫКЛ'}")
        except Exception:
            pass

    def _update_selected_status(self) -> None:
        self._update_adapter_line()
        self._update_link_line()
        self._update_coord_line()

    def _update_link_status(self) -> None:
        self._update_link_line()

    def _update_adapter_line(self) -> None:
        panel = self.query_one("#device_adapter_line", Static)
        value = self._get_selected_value()
        if not value:
            panel.update("Адаптер: —")
            return
        dev = getattr(self, "_device_map", {}).get(value) or {}
        if not isinstance(dev, dict):
            panel.update(f"Адаптер: {value}")
            return
        usb_id = dev.get("usb_id", "-")
        desc = dev.get("description", "Unknown")
        real = dev.get("path", value)
        panel.update("\n".join([
            f"Адаптер: {desc}",
            f"USB: {usb_id}",
            f"Порт: {real}",
        ]))

    def _update_link_line(self) -> None:
        panel = self.query_one("#device_link_line", Static)
        value = self._get_selected_value()
        use_link = bool(getattr(self, "_use_link", False))
        if Path("/dev/zigbee").exists() or Path("/dev/zigbee").is_symlink():
            try:
                link_target = os.path.realpath("/dev/zigbee")
            except Exception:
                link_target = "/dev/zigbee"
            link_part = link_target
        else:
            link_part = "нет"

        save_to = "/dev/zigbee" if use_link else (value or "—")
        panel.update("\n".join([
            f"/dev/zigbee: {link_part}",
            f"Сохранится: {save_to}",
        ]))

    def _update_coord_line(self, override: Optional[str] = None) -> None:
        panel = self.query_one("#device_coord_line", Static)
        if override is not None:
            panel.update(override)
            return
        selected = self._get_selected_value()
        if not selected:
            panel.update("Координатор: —")
            return
        device_info = getattr(self, "_device_map", {}).get(selected, {"path": selected, "description": "Unknown"})
        guess = guess_driver_from_device_info(device_info)
        probe_res = getattr(self, "_probe_results", {}).get(selected)
        if probe_res is None:
            panel.update("\n".join([
                f"Координатор: {guess.driver} ({guess.confidence})",
                "Probe: —",
            ]))
            return
        ok = "OK" if probe_res.get("ok") else "FAIL"
        driver = probe_res.get("driver") or guess.driver
        details = probe_res.get("details") or {}
        fw = None
        if isinstance(details, dict):
            if isinstance(details.get("version"), dict):
                ver = details["version"]
                fw = f"znp {ver.get('majorrel')}.{ver.get('minorrel')}.{ver.get('maintrel')} rev={ver.get('revision')}"
            elif details.get("firmware"):
                fw = f"ember {details.get('firmware')}"
        panel.update("\n".join([
            f"Координатор: {guess.driver} ({guess.confidence})",
            f"Probe: {ok} {driver}",
            f"FW: {fw}" if fw else "FW: —",
        ]))

    def _build_coordinator_details(self, selected_value: Optional[str]) -> str:
        if not selected_value:
            return "Выберите устройство, чтобы увидеть информацию о координаторе."

        device_info = getattr(self, "_device_map", {}).get(
            selected_value, {"path": selected_value, "description": "Unknown"}
        )
        guess = guess_driver_from_device_info(device_info)
        usb_id = device_info.get("usb_id", "-")
        desc = device_info.get("description", "Unknown")

        probe_res = getattr(self, "_probe_results", {}).get(selected_value)
        lines = [
            f"Устройство: {selected_value}",
            f"USB ID: {usb_id}",
            f"Описание: {desc}",
            "",
            f"Оценка: {guess.driver} ({guess.confidence})",
            f"Причина: {guess.reason}",
        ]
        if probe_res is not None:
            ok = "OK" if probe_res.get("ok") else "FAIL"
            lines.extend(["", f"Probe: {ok} {probe_res.get('driver')}", f"Сообщение: {probe_res.get('message')}"])
            details = probe_res.get("details") or {}
            if isinstance(details, dict):
                if isinstance(details.get("version"), dict):
                    ver = details["version"]
                    rev = ver.get("revision")
                    maj = ver.get("majorrel")
                    minr = ver.get("minorrel")
                    maint = ver.get("maintrel")
                    if rev is not None or maj is not None:
                        lines.append(f"Прошивка(ZNP): rev={rev} ver={maj}.{minr}.{maint}")
                if details.get("firmware"):
                    lines.append(f"Прошивка(Ember): {details.get('firmware')}")
        else:
            lines.extend(["", "Probe: (не выполнялся)"])
        return "\n".join(lines)

    def _update_coordinator_summary(self) -> None:
        self._update_coord_line()

    def on_mount(self) -> None:
        try:
            # 1) обновим кэш устройств (device_map)
            self._refresh_devices_cache()

            # 2) режим /dev/zigbee из конфига
            cfg = self.app.config.zigbee_device
            self._set_use_link(cfg == "/dev/zigbee")

            # 3) выделение текущего устройства (если в конфиге /dev/zigbee — подсветим реальный порт)
            selected: Optional[str] = None
            if cfg and cfg != "/dev/zigbee":
                selected = cfg
            else:
                try:
                    if Path("/dev/zigbee").exists() or Path("/dev/zigbee").is_symlink():
                        real = str(Path("/dev/zigbee").resolve())
                    else:
                        real = None
                except Exception:
                    real = None
                if real:
                    # предпочтём значение (by-id) которое соответствует этому реальному пути
                    for val, dev in getattr(self, "_device_map", {}).items():
                        if isinstance(dev, dict) and dev.get("path") == real:
                            selected = val
                            break

            self._set_selected_device(selected)
            self._update_selected_status()
            self._update_link_status()
            self._update_coordinator_summary()
            try:
                actions = self.query_one("#device_actions", ListView)
                actions.focus()
                actions.index = 0
            except Exception:
                pass
        except Exception:
            pass

    def on_save(self) -> None:
        selected = self._get_selected_value()
        if getattr(self, "_use_link", False):
            # сохраняем /dev/zigbee, но только если он реально существует
            if not (Path("/dev/zigbee").exists() or Path("/dev/zigbee").is_symlink()):
                self.app.notify("⚠️ Сначала создайте /dev/zigbee (кнопка «Сделать /dev/zigbee…»)", severity="warning")
                return
            self.app.config.zigbee_device = "/dev/zigbee"
        else:
            if not selected:
                self.app.notify("⚠️ Выберите устройство", severity="warning")
                return
            self.app.config.zigbee_device = selected
            self.app.config.save_config()
            self.app.notify("✅ Устройство сохранено")
            self.app.refresh_status()
            self.app.pop_screen()
            self.app.prompt_restart_if_running()
            return

        # use_link branch
        self.app.config.save_config()
        self.app.notify("✅ Устройство сохранено (/dev/zigbee)")
        self.app.refresh_status()
        self.app.pop_screen()
        self.app.prompt_restart_if_running()

    def action_save_and_exit(self) -> None:
        self.on_save()

    def on_refresh(self) -> None:
        self._refresh_devices_cache()
        cur = self._get_selected_value()
        if cur and cur not in getattr(self, "_device_map", {}):
            self._set_selected_device(None)
        self._update_selected_status()
        self._update_link_status()
        self._update_coordinator_summary()
        self.app.notify("🔍 Список обновлён")

    def _refresh_devices_cache(self) -> None:
        self._get_device_options()

    def on_make_zigbee_link(self) -> None:
        value = self._get_selected_value()
        if not value:
            self.app.notify("⚠️ Выберите устройство, на которое сделать /dev/zigbee", severity="warning")
            return
        target = value
        if not Path(target).exists():
            # На всякий случай, если выбрали by-id который исчез
            dev = getattr(self, "_device_map", {}).get(target)
            if isinstance(dev, dict) and dev.get("path"):
                target = dev["path"]
        if not Path(target).exists():
            self.app.notify(f"❌ Целевое устройство не существует: {target}", severity="error")
            return

        self._run_in_terminal(
            "Создание/обновление /dev/zigbee",
            "set -euo pipefail; "
            f"TARGET={str(target)!r}; "
            "echo \"target: $TARGET\"; "
            "sudo ln -sfn \"$TARGET\" /dev/zigbee; "
            "echo; "
            "ls -la /dev/zigbee || true; "
            "echo; "
            "readlink -f /dev/zigbee 2>/dev/null || true",
        )
        self._update_link_status()

    async def on_probe_driver(self) -> None:
        value = self._get_selected_value()
        if not value:
            self.app.notify("⚠️ Выберите устройство для probe", severity="warning")
            return

        device_info = getattr(self, "_device_map", {}).get(value, {"path": value, "description": "Unknown"})
        self._update_coord_line(f"⏳ Probe порта {value}... (Если Zigbee2MQTT запущен — остановите сервисы и повторите)")

        # running probe in background thread
        res = await asyncio.to_thread(probe_coordinator, device_info, value)
        # сохраняем результат на это устройство
        if not hasattr(self, "_probe_results"):
            self._probe_results = {}
        self._probe_results[value] = {
            "ok": res.ok,
            "driver": res.driver,
            "message": res.message,
            "details": res.details,
        }
        self._update_coord_line()

    @on(ListView.Selected, "#device_actions")
    async def on_action_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id == "act_pick_device":
            self._open_device_picker()
        elif item_id == "act_make_link":
            self.on_make_zigbee_link()
        elif item_id == "act_toggle_link":
            self._set_use_link(not getattr(self, "_use_link", False))
            self._update_selected_status()
            self._update_link_status()
            self._update_coordinator_summary()
        elif item_id == "act_probe":
            await self.on_probe_driver()
        elif item_id == "act_save":
            self.on_save()
        elif item_id == "act_back":
            self.app.pop_screen()

    def _open_device_picker(self) -> None:
        current = self._get_selected_value()

        def _on_pick(val: Optional[str]) -> None:
            self._set_selected_device(val)
            self._update_selected_status()
            self._update_link_status()
            self._update_coordinator_summary()

        def _get_items() -> tuple[list[str], list[Optional[str]]]:
            options = self._get_device_options()
            values: List[Optional[str]] = [None] + [v for _label, v in options]
            labels: List[str] = ["— Не выбрано —"] + [label for label, _v in options]
            return labels, values

        self.app.push_screen(DevicePickScreen(get_items=_get_items, current=current, on_pick=_on_pick))

    def action_back(self) -> None:
        self.app.pop_screen()


class DevicePickScreen(Screen):
    """Экран выбора устройства (список)."""

    BINDINGS = [Binding("escape", "back", "Назад")]

    def __init__(
        self,
        get_items: Callable[[], tuple[list[str], list[Optional[str]]]],
        current: Optional[str],
        on_pick: Callable[[Optional[str]], None],
    ):
        super().__init__()
        self._get_items = get_items
        self._labels: List[str] = []
        self._values: List[Optional[str]] = []
        self._current = current
        self._on_pick = on_pick

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("🔌 Выбор Zigbee USB адаптера", classes="screen-title")
            with ListView(id="pick_list"):
                # наполняем в on_mount (динамически)
                pass
        yield Footer()

    def _mount_items(self) -> None:
        labels, values = self._get_items()
        self._labels = labels
        self._values = values

        lv = self.query_one("#pick_list", ListView)
        # пересобираем список целиком через clear(), чтобы избежать гонок/дубликатов id
        lv.clear()

        # Важно: без id у элементов, чтобы исключить DuplicateIds при быстром refresh.
        lv.mount(ListItem(Label("🔍 Обновить список")))
        for label in self._labels:
            lv.mount(ListItem(Label(label)))
        lv.mount(ListItem(Label("↩ Назад")))

    def on_mount(self) -> None:
        lv = self.query_one("#pick_list", ListView)
        lv.focus()
        self._mount_items()
        try:
            if self._current in self._values:
                # +1 из-за пункта "Обновить список"
                lv.index = self._values.index(self._current) + 1
            else:
                lv.index = 1  # первый реальный элемент, после refresh
        except Exception:
            pass

    @on(ListView.Selected, "#pick_list")
    def on_selected(self, event: ListView.Selected) -> None:
        lv = self.query_one("#pick_list", ListView)
        idx = lv.index or 0

        # 0 = refresh, last = back, else = item
        if idx == 0:
            self._mount_items()
            # фидбек + вернуть фокус
            try:
                count = max(0, len(self._labels) - 1)  # без "— Не выбрано —"
            except Exception:
                count = 0
            try:
                self.app.notify(f"🔍 Список обновлён (устройств: {count})")
            except Exception:
                pass
            try:
                lv.focus()
                lv.index = 1
            except Exception:
                pass
            return

        if idx == len(self._labels) + 1:
            self.app.pop_screen()
            return

        # элементы устройств: 1..len(labels)
        val_idx = idx - 1
        val = self._values[val_idx] if 0 <= val_idx < len(self._values) else None
        self._on_pick(val)
        self.app.pop_screen()

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

            yield Static("Протокол:", classes="config-label")
            yield Select(
                options=[
                    ("MQTT 3.1 (mqttv31)", "mqttv31"),
                    ("MQTT 3.1.1 (mqttv311)", "mqttv311"),
                    ("MQTT 5.0 (mqttv50)", "mqttv50"),
                ],
                id="cloud_proto",
                allow_blank=False,
            )

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
        try:
            self.query_one("#cloud_proto", Select).value = config.cloud_mqtt_protocol
        except Exception:
            pass
        self.query_one("#cloud_user", Input).value = config.cloud_mqtt_user
        self.query_one("#cloud_password", Input).value = config.cloud_mqtt_password

    @on(Button.Pressed, "#save_btn")
    def on_save(self) -> None:
        config = self.app.config
        config.cloud_mqtt_enabled = self.query_one("#cloud_enabled", Switch).value
        config.cloud_mqtt_host = self.query_one("#cloud_host", Input).value
        try:
            config.cloud_mqtt_protocol = self.query_one("#cloud_proto", Select).value or config.cloud_mqtt_protocol
        except Exception:
            pass
        config.cloud_mqtt_user = self.query_one("#cloud_user", Input).value
        config.cloud_mqtt_password = self.query_one("#cloud_password", Input).value
        config.save_config()
        if getattr(config, "bridge_conf_last_error", None):
            self.app.notify(f"⚠️ bridge.conf не обновлён: {config.bridge_conf_last_error}", severity="warning")
        else:
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
        usf = shutil.which("universal-silabs-flasher")

        devices = []
        for p in ("/dev/ttyUSB0", "/dev/ttyACM0"):
            if Path(p).exists():
                devices.append(p)

        lines = [
            f"[b]dialout:[/b] {'✅' if in_dialout else '❌'}",
            f"[b]udev rules:[/b] {'✅' if rules_installed else '❌'} ({self._rules_dst()})",
            f"[b]/dev nodes:[/b] {', '.join(devices) if devices else 'не найдены'}",
            f"[b]universal-silabs-flasher:[/b] {'✅' if usf else '❌'} ({usf or 'не установлен'})",
        ]
        panel.update("\n".join(lines))

    def _run_in_terminal(self, title: str, command: str) -> None:
        """Выполнить команду в реальном терминале (для sudo)."""
        with self.app.suspend():
            print(f"\n{'='*60}\n{title}\n{'='*60}\n")
            # Важно: используем /bin/bash для редиректов/глобов
            os.system("/bin/bash -lc " + shlex.quote(command))
            input("\nНажмите Enter для возврата в TUI...")
        self._refresh_status()

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("🔐 Доступ к USB (Zigbee адаптер)", classes="screen-title")
            yield Static(id="usb_status")
            yield Static("Некоторые действия требуют sudo (после dialout может понадобиться перелогин).", classes="config-hint")

            with ListView(id="usb_actions"):
                yield ListItem(Label("➕ Добавить в dialout"), id="usb_add_dialout")
                yield ListItem(Label("📄 Установить udev-правила"), id="usb_install_rules")
                yield ListItem(Label("🔄 Reload udev"), id="usb_reload_udev")
                yield ListItem(Label("⬇ Установить universal-silabs-flasher"), id="usb_install_usf")
                yield ListItem(Label("▶ Выполнить всё"), id="usb_run_all")
                yield ListItem(Label("↩ Назад"), id="usb_back")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_status()
        try:
            actions = self.query_one("#usb_actions", ListView)
            actions.focus()
            actions.index = 0
        except Exception:
            pass

    def _do_add_dialout(self) -> None:
        user = getpass.getuser()
        self._run_in_terminal(
            "Добавление пользователя в группу dialout",
            f"sudo usermod -aG dialout {user} && echo && echo 'Готово. Перелогиньтесь или выполните: newgrp dialout'"
        )

    def _do_install_rules(self) -> None:
        src = self._rules_src()
        if not src.exists():
            self.app.notify(f"❌ Не найден файл правил: {src}", severity="error")
            return
        self._run_in_terminal(
            "Установка udev-правил для Zigbee адаптера",
            f"sudo cp {str(src)!r} /etc/udev/rules.d/99-zigbee.rules && sudo udevadm control --reload-rules && sudo udevadm trigger"
        )

    def _do_reload_udev(self) -> None:
        self._run_in_terminal(
            "Перезагрузка udev правил",
            "sudo udevadm control --reload-rules && sudo udevadm trigger"
        )

    def _do_run_all(self) -> None:
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

    async def _do_install_usf(self) -> None:
        self.app.notify("⏳ Устанавливаю universal-silabs-flasher…")
        res = await asyncio.to_thread(install_universal_silabs_flasher)
        self._refresh_status()
        if res.ok:
            self.app.notify(f"✅ {res.message}")
            return
        self.app.notify(f"❌ {res.message}", severity="error")
        if res.output:
            with self.app.suspend():
                print("\n" + "=" * 60)
                print("universal-silabs-flasher install output")
                print("=" * 60 + "\n")
                print(res.output)
                input("\nНажмите Enter для возврата в TUI...")

    @on(ListView.Selected, "#usb_actions")
    async def on_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id == "usb_back":
            self.app.pop_screen()
            return
        if item_id == "usb_add_dialout":
            self._do_add_dialout()
        elif item_id == "usb_install_rules":
            self._do_install_rules()
        elif item_id == "usb_reload_udev":
            self._do_reload_udev()
        elif item_id == "usb_install_usf":
            await self._do_install_usf()
        elif item_id == "usb_run_all":
            self._do_run_all()

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
                yield ListItem(Label("🗂️ Конфиги"), id="menu_configs")
                yield ListItem(Label("🔓 permit_join: ВЫКЛ"), id="menu_permit_join")
                yield ListItem(Label("🗑️ Удалить контейнеры"), id="menu_down")
                yield ListItem(Label("💀 Зачистить контейнеры (с volume)"), id="menu_purge")
                yield ListItem(Label("↩ Назад"), id="menu_back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#control_menu", ListView).focus()
        self.query_one("#control_menu", ListView).index = 0
        self._update_permit_join_label()

    def _update_permit_join_label(self) -> None:
        try:
            item = self.query_one("#menu_permit_join", ListItem)
            label = item.query_one(Label)
        except Exception:
            return
        cur = self.app.config.get_z2m_permit_join()
        if cur is None:
            label.update("🔓 permit_join (yaml): ?")
        else:
            label.update(f"🔓 permit_join (yaml): {'ВКЛ' if cur else 'ВЫКЛ'}")

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
        elif item_id == "menu_configs":
            self.app.push_screen(ConfigFilesScreen())
        elif item_id == "menu_permit_join":
            cur = self.app.config.get_z2m_permit_join()
            # если неизвестно — считаем, что сейчас выключено
            enabled = not bool(cur)
            ok = await asyncio.to_thread(self.app.config.set_z2m_permit_join, enabled)
            if ok:
                self.app.notify(f"✅ permit_join (yaml): {'ВКЛ' if enabled else 'ВЫКЛ'}")
                # чтобы изменения применились — предложим restart если контейнеры запущены
                self.app.refresh_status()
                self.app.prompt_restart_if_running()
            else:
                self.app.notify("❌ Не удалось обновить zigbee2mqtt.yaml (проверьте права)", severity="error")
            self._update_permit_join_label()
        elif item_id == "menu_down":
            self.app.push_screen(ConfirmDownScreen())
        elif item_id == "menu_purge":
            self.app.push_screen(ConfirmPurgeScreen())

    def action_back(self) -> None:
        self.app.pop_screen()


class ConfirmConfigOverwriteScreen(ArrowNavScreen):
    """Подтверждение перезаписи конфигов из шаблонов."""

    BINDINGS = [Binding("escape", "back", "Отмена")]

    def __init__(self, title: str, message: str, on_yes):
        super().__init__()
        self._title = title
        self._message = message
        self._on_yes = on_yes

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(self._title, classes="screen-title")
            yield Static(self._message, classes="config-hint")
            with Horizontal(classes="button-row"):
                yield Button("✅ Продолжить", id="cfg_overwrite_yes", variant="primary")
                yield Button("❌ Отмена", id="cfg_overwrite_no", variant="default")
        yield Footer()

    @on(Button.Pressed, "#cfg_overwrite_yes")
    async def on_yes(self) -> None:
        self.app.pop_screen()
        await self._on_yes()

    @on(Button.Pressed, "#cfg_overwrite_no")
    def on_no(self) -> None:
        self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()


class ConfigFilesScreen(Screen):
    """Экран обслуживания конфигов (генерация/восстановление)."""

    BINDINGS = [Binding("escape", "back", "Назад")]

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("🗂️ Конфиги", classes="screen-title")
            yield Static(
                "Здесь можно создать отсутствующие конфиги или восстановить их из шаблонов.\n"
                "Генерация идёт из template-файлов (Jinja2) и переменных .env.\n"
                "Перезапись делает backup рядом с файлом (.bak-YYYYmmdd-HHMMSS).",
                classes="config-hint",
            )
            with ListView(id="configs_menu"):
                yield ListItem(Label("🧩 Создать отсутствующие (safe)"), id="cfg_safe")
                yield ListItem(Label("♻️ Пересоздать из шаблонов (force + backup)"), id="cfg_force")
                yield ListItem(Label("📦 Перенести devices в отдельный файл"), id="cfg_devices")
                yield ListItem(Label("↩ Назад"), id="cfg_back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#configs_menu", ListView).focus()
        self.query_one("#configs_menu", ListView).index = 0

    def _notify_results(self, res: dict) -> None:
        ok = all(bool(v.get("ok")) for v in res.values()) if res else True
        if ok:
            self.app.notify("✅ Готово")
        else:
            self.app.notify("⚠️ Есть ошибки (подробности в консоли/файлах)", severity="warning")

    def _should_prompt_restart(self, res: dict) -> bool:
        """Нужен ли prompt на перезапуск: если меняли конфиги, которые читает контейнер."""
        try:
            z = res.get("zigbee2mqtt.yaml") if isinstance(res, dict) else None
            b = res.get("bridge.conf") if isinstance(res, dict) else None
            touched = False
            if isinstance(z, dict) and z.get("status") in ("created", "overwritten"):
                touched = True
            if isinstance(b, dict) and b.get("status") in ("created", "overwritten"):
                touched = True
            return touched
        except Exception:
            return False

    async def _do_safe(self) -> None:
        res = await asyncio.to_thread(
            self.app.config.generate_local_configs,
            force=False,
            backup=True,
            zigbee2mqtt_yaml=True,
            bridge_conf=True,
            split_yaml=False,
        )
        self._notify_results(res)
        if self._should_prompt_restart(res):
            self.app.prompt_restart_if_running()

    async def _do_force(self) -> None:
        res = await asyncio.to_thread(
            self.app.config.generate_local_configs,
            force=True,
            backup=True,
            zigbee2mqtt_yaml=True,
            bridge_conf=True,
            split_yaml=False,
        )
        self._notify_results(res)
        if self._should_prompt_restart(res):
            self.app.prompt_restart_if_running()

    async def _do_devices(self) -> None:
        res = await asyncio.to_thread(self.app.config.extract_devices_to_file, backup=True)
        self._notify_results({"devices": res})

    @on(ListView.Selected)
    async def on_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id == "cfg_back":
            self.app.pop_screen()
            return

        if item_id == "cfg_safe":
            await self._do_safe()
            return

        if item_id == "cfg_devices":
            await self._do_devices()
            return

        if item_id == "cfg_force":
            msg = (
                "Будут перезаписаны:\n"
                f"- {self.app.config.zigbee2mqtt_yaml}\n"
                f"- {self.app.config.bridge_conf}\n\n"
                "Перед перезаписью будет создан backup рядом с каждым файлом.\n"
                "Продолжить?"
            )
            self.app.push_screen(
                ConfirmConfigOverwriteScreen("♻️ Перезаписать конфиги?", msg, on_yes=self._do_force)
            )

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


class ConfirmPurgeScreen(ArrowNavScreen):
    """Подтверждение очистки контейнеров вместе с volume (docker-compose down -v)."""

    BINDINGS = [Binding("escape", "back", "Отмена")]

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("💀 Зачистить контейнеры и volume?", classes="screen-title")
            yield Static(
                "Это выполнит docker-compose down -v.\n"
                "Будут удалены контейнеры И volume (данные Mosquitto/Zigbee2MQTT/NodeRED).\n"
                "Действие необратимо.",
                classes="config-hint",
            )
            with Horizontal(classes="button-row"):
                yield Button("💀 Зачистить", id="confirm_purge_yes", variant="error")
                yield Button("❌ Отмена", id="confirm_purge_no", variant="default")
        yield Footer()

    def on_mount(self) -> None:
        try:
            self.query_one("#confirm_purge_yes", Button).focus()
        except Exception:
            pass

    @on(Button.Pressed, "#confirm_purge_yes")
    async def on_yes(self) -> None:
        self.app.pop_screen()
        await self.app.run_docker_operation("💀 Зачистка контейнеров и volume", self.app._do_purge)

    @on(Button.Pressed, "#confirm_purge_no")
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

    #device_screen_root {
        /* убрать общий отступ Container, чтобы меню можно было поднять */
        margin: 0;
        padding: 0;
    }

    #device_title {
        /* уменьшить высоту заголовка именно на экране выбора донгла */
        margin: 0;
        padding: 0 1;
    }

    #device_adapter_line {
        margin: 0 1;
        padding: 0;
        text-wrap: wrap;
    }

    #device_link_line {
        margin: 0 1;
        padding: 0;
        text-wrap: wrap;
    }

    #device_coord_line {
        margin: 0 1;
        padding: 0;
        text-wrap: wrap;
    }

    #device_actions {
        /* меню на экране выбора донгла — без внутреннего скролла */
        height: 9;
        margin: 0 1;
        padding: 0;
        overflow-y: hidden;
        scrollbar-size: 0 0;
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
            with self.suspend():
                def wait_enter(prompt: str = "Нажмите Enter для возврата в меню...") -> None:
                    # Стараемся читать из /dev/tty (надежнее в suspend), иначе fallback на stdin.
                    try:
                        os.system(
                            "/bin/bash -lc "
                            + shlex.quote(f"read -r -p {prompt!r} _ </dev/tty >/dev/tty")
                        )
                        return
                    except Exception:
                        pass
                    try:
                        print(prompt)
                        input()
                    except Exception:
                        pass

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
                print()
                wait_enter()

            self.refresh_status()

        except Exception as e:
            with self.suspend():
                print(f"\n❌ Критическая ошибка: {e}")
                print()
                try:
                    os.system(
                        "/bin/bash -lc "
                        + shlex.quote("read -r -p 'Нажмите Enter для возврата в меню...' _ </dev/tty >/dev/tty")
                    )
                except Exception:
                    pass

    def _do_start(self, log_callback) -> bool:
        return self.docker_manager.start_services(log_callback)

    def _do_stop(self, log_callback) -> bool:
        return self.docker_manager.stop_services(log_callback)

    def _do_restart(self, log_callback) -> bool:
        return self.docker_manager.restart_services(log_callback)

    def _do_down(self, log_callback) -> bool:
        return self.docker_manager.down_services(log_callback)

    def _do_purge(self, log_callback) -> bool:
        return self.docker_manager.down_services_with_volumes(log_callback)

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
        device_error = self.app.config.get_device_error()
        if device_error:
            self.app.notify(f"⚠️ {device_error}", severity="error")
            self.app.pop_screen()
            self.app.push_screen(DeviceScreen())
            return

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
    # Textual включает mouse-tracking по умолчанию, из-за чего в некоторых терминалах
    # перестают работать привычные выделение/копирование и вставка правой кнопкой.
    # Отключаем mouse по умолчанию, но оставляем совместимость со старыми версиями.
    try:
        app.run(mouse=False)
    except TypeError:
        app.run()
