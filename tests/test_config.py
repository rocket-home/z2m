"""
Тесты конфигурации Z2MConfig.

Главное, что они страхуют:
- round-trip yaml сохраняет top-level ключи (`availability`, `advanced.last_seen`) и `devices`
  — это инвариант, на котором держится включение availability через шаблон/правку конфига;
- шаблон рендерит `availability` + `advanced.last_seen`;
- `_merge_env_file` сохраняет неизвестные ключи и комментарии .env.
"""
import shutil
from pathlib import Path

import pytest
import yaml

from z2m_manager.config import Z2MConfig

REPO = Path(__file__).resolve().parent.parent  # .../zigbee/z2m


@pytest.fixture
def base(tmp_path):
    """Минимальный layout репо во временном каталоге (с копией templates/)."""
    (tmp_path / "mosquitto" / "conf.d").mkdir(parents=True)
    tpl_dst = tmp_path / "z2m_manager" / "templates"
    tpl_dst.parent.mkdir(parents=True)
    shutil.copytree(REPO / "z2m_manager" / "templates", tpl_dst)
    return tmp_path


def test_save_preserves_top_level_keys(base):
    y = base / "zigbee2mqtt.yaml"
    y.write_text(
        "homeassistant: false\n"
        "permit_join: true\n"
        "serial:\n  port: /dev/zigbee\n"
        "frontend:\n  port: 4000\n  host: 0.0.0.0\n"
        "advanced:\n  log_level: warn\n  last_seen: ISO_8601\n"
        "availability:\n  active:\n    timeout: 10\n  passive:\n    timeout: 1500\n"
        "devices:\n  '0xAAAA':\n    friendly_name: '0xAAAA'\n",
        encoding="utf-8",
    )
    cfg = Z2MConfig(base_dir=base)
    cfg._save_zigbee2mqtt_config()

    data = yaml.safe_load(y.read_text(encoding="utf-8"))
    assert data["availability"]["active"]["timeout"] == 10
    assert data["availability"]["passive"]["timeout"] == 1500
    assert data["advanced"]["last_seen"] == "ISO_8601"
    assert "0xAAAA" in data["devices"]

    # devices вынесены в отдельный файл, base — без devices, но с availability
    base_data = yaml.safe_load((base / "zigbee2mqtt.base.yaml").read_text(encoding="utf-8"))
    dev_data = yaml.safe_load((base / "zigbee2mqtt.devices.yaml").read_text(encoding="utf-8"))
    assert "availability" in base_data and "devices" not in base_data
    assert "0xAAAA" in dev_data


def test_template_render_has_availability_and_last_seen(base):
    cfg = Z2MConfig(base_dir=base)
    res = cfg.generate_local_configs(
        force=True, backup=False, zigbee2mqtt_yaml=True, bridge_conf=False
    )
    assert res["zigbee2mqtt.yaml"]["ok"], res
    data = yaml.safe_load((base / "zigbee2mqtt.yaml").read_text(encoding="utf-8"))
    assert "availability" in data
    assert data["advanced"]["last_seen"] == "ISO_8601"


def test_env_merge_preserves_unknown_keys_and_comments(base):
    env = base / ".env"
    env.write_text("# my header\nMQTT_USER=user\nCUSTOM_KEY=keepme\n", encoding="utf-8")
    cfg = Z2MConfig(base_dir=base)
    cfg._config["MQTT_USER"] = "changed"
    cfg.save_config()

    text = env.read_text(encoding="utf-8")
    assert "CUSTOM_KEY=keepme" in text   # неизвестный ключ сохранён
    assert "# my header" in text          # комментарий сохранён
    assert "MQTT_USER=changed" in text    # управляемый ключ обновлён


def test_get_base_topic_and_permit_join(base):
    (base / "zigbee2mqtt.yaml").write_text(
        "mqtt:\n  base_topic: zigbee2mqtt\npermit_join: true\n", encoding="utf-8"
    )
    cfg = Z2MConfig(base_dir=base)
    assert cfg.get_z2m_base_topic() == "zigbee2mqtt"
    assert cfg.get_z2m_permit_join() is True
