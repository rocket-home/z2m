"""
Утилиты для проверки подключения к MQTT
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


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


