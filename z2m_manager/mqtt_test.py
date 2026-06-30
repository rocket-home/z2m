"""
Утилиты для проверки подключения к MQTT
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import json

from .config import Z2MConfig


@dataclass
class MqttTestResult:
    ok: bool
    message: str
    host: str
    port: int


def test_mqtt_connection(
    host: str,
    username: str,
    password: str,
    port: int = 1883,
    timeout_sec: int = 5,
) -> MqttTestResult:
    """
    Проверка подключения к MQTT с кредами.
    Делает connect+disconnect, без публикаций.
    """
    try:
        import paho.mqtt.client as mqtt  # type: ignore
    except Exception:
        return MqttTestResult(
            ok=False,
            message="Не установлена зависимость paho-mqtt (обновите зависимости через ./z2m).",
            host=host,
            port=port,
        )

    import socket
    import threading
    import time

    connected = threading.Event()
    result_message: Optional[str] = None

    def on_connect(client, userdata, flags, rc, properties=None):  # pragma: no cover
        nonlocal result_message
        if rc == 0:
            result_message = "Подключение успешно"
            connected.set()
        else:
            result_message = f"Ошибка подключения (rc={rc})"
            connected.set()

    client = mqtt.Client()
    client.username_pw_set(username=username, password=password)
    client.on_connect = on_connect

    try:
        client.connect(host, port, keepalive=10)
        client.loop_start()

        start = time.time()
        while time.time() - start < timeout_sec:
            if connected.is_set():
                break
            time.sleep(0.05)

        if not connected.is_set():
            return MqttTestResult(
                ok=False,
                message=f"Таймаут подключения за {timeout_sec} сек",
                host=host,
                port=port,
            )

        ok = (result_message == "Подключение успешно")
        return MqttTestResult(
            ok=ok,
            message=result_message or "Неизвестный результат",
            host=host,
            port=port,
        )
    except socket.gaierror:
        return MqttTestResult(ok=False, message="Не удалось разрешить имя хоста", host=host, port=port)
    except ConnectionRefusedError:
        return MqttTestResult(ok=False, message="Соединение отклонено", host=host, port=port)
    except Exception as e:
        return MqttTestResult(ok=False, message=f"Ошибка: {e}", host=host, port=port)
    finally:
        try:
            client.loop_stop()
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass


@dataclass
class MqttPublishResult:
    ok: bool
    message: str
    topic: str


def publish_mqtt_message(
    host: str,
    port: int,
    topic: str,
    payload: str,
    username: str = "",
    password: str = "",
    timeout_sec: int = 5,
) -> MqttPublishResult:
    """
    Подключается к MQTT, публикует сообщение и отключается.
    """
    try:
        import paho.mqtt.client as mqtt  # type: ignore
    except Exception:
        return MqttPublishResult(
            ok=False,
            message="Не установлена зависимость paho-mqtt (обновите зависимости через ./z2m).",
            topic=topic,
        )

    import socket
    import threading
    import time

    connected = threading.Event()
    published = threading.Event()
    last_error: Optional[str] = None

    def on_connect(client, userdata, flags, rc, properties=None):  # pragma: no cover
        nonlocal last_error
        if rc == 0:
            connected.set()
        else:
            last_error = f"Ошибка подключения (rc={rc})"
            connected.set()

    def on_publish(client, userdata, mid):  # pragma: no cover
        published.set()

    client = mqtt.Client()
    if username and password:
        client.username_pw_set(username=username, password=password)
    client.on_connect = on_connect
    client.on_publish = on_publish

    try:
        client.connect(host, port, keepalive=10)
        client.loop_start()

        start = time.time()
        while time.time() - start < timeout_sec:
            if connected.is_set():
                break
            time.sleep(0.05)

        if not connected.is_set():
            return MqttPublishResult(ok=False, message=f"Таймаут подключения за {timeout_sec} сек", topic=topic)
        if last_error:
            return MqttPublishResult(ok=False, message=last_error, topic=topic)

        client.publish(topic, payload=payload, qos=0, retain=False)

        start = time.time()
        while time.time() - start < timeout_sec:
            if published.is_set():
                break
            time.sleep(0.05)
        if not published.is_set():
            return MqttPublishResult(ok=False, message=f"Таймаут публикации за {timeout_sec} сек", topic=topic)

        return MqttPublishResult(ok=True, message="Сообщение опубликовано", topic=topic)
    except socket.gaierror:
        return MqttPublishResult(ok=False, message="Не удалось разрешить имя хоста", topic=topic)
    except ConnectionRefusedError:
        return MqttPublishResult(ok=False, message="Соединение отклонено", topic=topic)
    except Exception as e:
        return MqttPublishResult(ok=False, message=f"Ошибка: {e}", topic=topic)
    finally:
        try:
            client.loop_stop()
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass


@dataclass
class AvailabilityResult:
    ok: bool
    message: str
    states: dict  # {friendly_name: "online"|"offline"|<raw>}


def _parse_availability_payload(payload: str) -> str:
    """z2m publishes either plain 'online'/'offline' (legacy) or JSON {"state": "..."}."""
    payload = (payload or "").strip()
    if payload.startswith("{"):
        try:
            return str(json.loads(payload).get("state", payload))
        except Exception:
            return payload
    return payload


def collect_availability(
    config: Z2MConfig,
    host: str = "127.0.0.1",
    port: int = 1883,
    timeout_sec: int = 5,
) -> AvailabilityResult:
    """
    Подписывается на <base_topic>/+/availability, собирает retained-статусы устройств
    за timeout_sec и возвращает {friendly_name: state}. Требует включённого
    availability в zigbee2mqtt.yaml (иначе словарь будет пустым).
    """
    try:
        import paho.mqtt.client as mqtt  # type: ignore
    except Exception:
        return AvailabilityResult(
            ok=False,
            message="Не установлена зависимость paho-mqtt (обновите зависимости через ./z2m).",
            states={},
        )

    import socket
    import threading
    import time

    base = config.get_z2m_base_topic()
    sub_topic = f"{base}/+/availability"
    prefix = f"{base}/"
    suffix = "/availability"

    connected = threading.Event()
    last_error: Optional[str] = None
    states: dict = {}

    def on_connect(client, userdata, flags, rc, properties=None):  # pragma: no cover
        nonlocal last_error
        if rc == 0:
            client.subscribe(sub_topic)
            connected.set()
        else:
            last_error = f"Ошибка подключения (rc={rc})"
            connected.set()

    def on_message(client, userdata, msg):  # pragma: no cover
        topic = msg.topic
        if topic.startswith(prefix) and topic.endswith(suffix):
            name = topic[len(prefix):-len(suffix)]
            try:
                states[name] = _parse_availability_payload(msg.payload.decode("utf-8", "replace"))
            except Exception:
                states[name] = "?"

    client = mqtt.Client()
    if config.mqtt_user and config.mqtt_password:
        client.username_pw_set(username=config.mqtt_user, password=config.mqtt_password)
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(host, port, keepalive=10)
        client.loop_start()

        start = time.time()
        while time.time() - start < timeout_sec:
            if connected.is_set():
                break
            time.sleep(0.05)
        if not connected.is_set():
            return AvailabilityResult(ok=False, message=f"Таймаут подключения за {timeout_sec} сек", states={})
        if last_error:
            return AvailabilityResult(ok=False, message=last_error, states={})

        # дать retained-сообщениям прийти
        time.sleep(min(timeout_sec, 3))
        return AvailabilityResult(ok=True, message=f"Собрано статусов: {len(states)}", states=states)
    except socket.gaierror:
        return AvailabilityResult(ok=False, message="Не удалось разрешить имя хоста", states={})
    except ConnectionRefusedError:
        return AvailabilityResult(ok=False, message="Соединение отклонено", states={})
    except Exception as e:
        return AvailabilityResult(ok=False, message=f"Ошибка: {e}", states={})
    finally:
        try:
            client.loop_stop()
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass


def set_z2m_permit_join(
    config: Z2MConfig,
    enabled: bool,
    duration_sec: int = 60,
    host: str = "127.0.0.1",
    port: int = 1883,
) -> MqttPublishResult:
    """
    Включить/выключить разрешение подключения новых устройств в Zigbee2MQTT.
    Команда идёт через MQTT: <base_topic>/bridge/request/permit_join
    """
    base = config.get_z2m_base_topic()
    topic = f"{base}/bridge/request/permit_join"
    if enabled:
        payload = json.dumps({"value": True, "time": int(duration_sec)})
    else:
        payload = json.dumps({"value": False})
    return publish_mqtt_message(
        host=host,
        port=port,
        topic=topic,
        payload=payload,
        username=config.mqtt_user,
        password=config.mqtt_password,
        timeout_sec=5,
    )

