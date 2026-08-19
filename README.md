# Zigbee2MQTT Environment

Окружение для запуска Zigbee2MQTT с локальным MQTT брокером и опциональным NodeRED.

## Требования

### Установка зависимостей (Ubuntu/Debian)

```bash
# Python 3.8+ и venv
sudo apt update
sudo apt install -y python3 python3-venv curl

# Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Перелогиниться или выполнить:
newgrp docker
```

### Настройка доступа к USB (Zigbee адаптер)

Это можно сделать прямо из TUI: **Настройки → 🔐 Доступ к USB** (кнопки выполняют команды через `sudo`).

```bash
# 1. Добавить пользователя в группу dialout
sudo usermod -aG dialout $USER
newgrp dialout

# 2. Установить udev-правила для Zigbee адаптеров
sudo cp 99-zigbee.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger

# 3. Проверить (после подключения адаптера)
ls -la /dev/ttyUSB* /dev/ttyACM* /dev/zigbee 2>/dev/null
```

После установки правил адаптер будет доступен как `/dev/zigbee` (симлинк).

Примечание: udev-правила полезны не только “линком” — они также обеспечивают:
- стабильное имя устройства (не зависит от того, станет оно `/dev/ttyUSB0` или `/dev/ttyUSB1`);
- корректные права доступа/группу (чтобы можно было работать без root, достаточно быть в `dialout`);
- при необходимости можно добавить доп. атрибуты (например, чтобы ModemManager не пытался “захватить” порт).

## Быстрый старт

```bash
cd z2m
./z2m
```

### Установка команды `z2m` в систему

Чтобы запускать менеджер из любой директории командой `z2m`:

```bash
cd z2m
./install.sh
```

При первом запуске автоматически:
- Установится `uv` (менеджер пакетов Python)
- Создастся виртуальное окружение
- Установятся все зависимости
- Запустится мастер первичной настройки (wizard)

### Режимы запуска

```bash
./z2m               # TUI режим (графический интерфейс в терминале)
./z2m --cli         # Интерактивный CLI режим

# Быстрые команды (без входа в интерфейс)
./z2m start         # Запустить сервисы
./z2m stop          # Остановить сервисы
./z2m restart       # Перезапустить
./z2m status        # Показать статус
./z2m logs          # Показать логи
./z2m logs mqtt     # Логи конкретного сервиса

# Диагностика
./z2m doctor        # Проверка системы
./z2m devices       # Список USB устройств
./z2m coordinator   # Определить тип координатора (ember/zstack) по USB
./z2m permit-join on|off  # Разрешить/запретить join (permit_join) в zigbee2mqtt.yaml
./z2m config        # Текущая конфигурация
```

## Компоненты

| Сервис | Порт | Описание |
|--------|------|----------|
| MQTT (Mosquitto) | 1883, 9001 | Локальный MQTT брокер |
| Zigbee2MQTT | 4000 | Zigbee шлюз с веб-интерфейсом |
| NodeRED | 1880 | Опциональный, для автоматизаций |

## Конфигурация

Менеджер создаёт файл `.env` с настройками (**локальный файл, не хранится в git**):

```env
MQTT_USER=user
MQTT_PASSWORD=your_password
ZIGBEE_DEVICE=/dev/ttyACM0
NODERED_ENABLED=false
CLOUD_MQTT_ENABLED=false
CLOUD_MQTT_HOST=mq.rocket-home.ru
CLOUD_MQTT_PORT=8883
CLOUD_MQTT_TLS=true
CLOUD_MQTT_USER=UUID
CLOUD_MQTT_PASSWORD=password
```

`CLOUD_MQTT_PORT`/`CLOUD_MQTT_TLS` задают, как мост идёт в облако. По умолчанию — TLS на 8883.
Открытый 1883 (`CLOUD_MQTT_TLS=false`) оставлен как аварийный вариант: по нему облако помечает
хаб незащищённым и **запрещает перенос автоматизаций на локальное исполнение**.

### Zigbee USB адаптер

Менеджер автоматически определяет USB Zigbee адаптеры. Поддерживаются:
- CC2531
- Sonoff Zigbee 3.0 (ZBDongle-P)
- SONOFF ZBDongle-E
- ConBee/ConBee II
- SLZB-06
- И другие совместимые адаптеры

### Облачный MQTT (бридж)

Для подключения к облачному MQTT серверу (например, `mq.rocket-home.ru`):

1. Включите облачный MQTT в настройках
2. Укажите хост (по умолчанию `mq.rocket-home.ru`)
3. Введите UUID пользователя и пароль
4. (опционально) Wizard может проверить подключение по введённым кредам и предложить включить бридж

Конфигурация бриджа сохраняется в `mosquitto/conf.d/bridge.conf`.
Этот файл тоже **локальный** (может содержать креды) — в репозитории хранится пример `mosquitto/conf.d/bridge.conf.example` (для ручной настройки).
Также есть template `z2m_manager/templates/bridge.conf.j2` — он используется менеджером для генерации `bridge.conf` из переменных `.env`.

### Node-RED (локальные автоматизации)

1. Включите Node-RED:
   - в TUI: **Настройки → 🔴 NodeRED → Вкл**
   - или CLI: `./z2m enable-nodered`

2. Запустите сервисы (если ещё не запущены): `./z2m start`

3. Откройте Node-RED: `http://<host>:1880`

#### Подключение Node-RED к локальному MQTT (Mosquitto)

В Node-RED добавьте ноду **MQTT in** или **MQTT out** → откройте настройки **Server** → **Add new mqtt-broker**:
- **Server**: `mqtt`
- **Port**: `1883`
- **Username**: значение `MQTT_USER` из `.env`
- **Password**: значение `MQTT_PASSWORD` из `.env`
  - по умолчанию: `user` / `password` (если вы не меняли их в настройках)

Примеры топиков Zigbee2MQTT:
- Подписаться на всё: `zigbee2mqtt/#`
- Команды устройствам: `zigbee2mqtt/<friendly_name>/set`
- Состояние устройства: `zigbee2mqtt/<friendly_name>`

### Zigbee2MQTT конфиг

Zigbee2MQTT использует `zigbee2mqtt.yaml` (он монтируется как `/app/data/configuration.yaml`).
После добавления устройств через веб-интерфейс Zigbee2MQTT может записывать туда параметры сети/ключи, поэтому `zigbee2mqtt.yaml` — **локальный файл**.
В репозитории хранится безопасный пример `zigbee2mqtt.yaml.example` (для ручной настройки).
При отсутствии `zigbee2mqtt.yaml` менеджер создаёт его автоматически:
- предпочитает template `z2m_manager/templates/zigbee2mqtt.yaml.j2` (рендерится из `.env` через Jinja2);

## Структура проекта

```
z2m/
├── z2m                    # Лаунчер (bash)
├── z2m.py                 # Точка входа (Python)
├── 99-zigbee.rules        # udev-правила для USB адаптеров
├── z2m_manager/           # Модуль менеджера
│   ├── cli.py             # CLI интерфейс
│   ├── tui.py             # TUI интерфейс (textual)
│   ├── config.py          # Работа с конфигурацией
│   ├── docker_manager.py  # Управление Docker
│   ├── device_detector.py # Автодетект USB устройств
│   ├── doctor.py          # Диагностика системы
│   ├── wizard.py          # Мастер первой настройки
│   └── requirements.txt   # Зависимости
├── docker-compose.yml     # Docker Compose конфигурация
├── zigbee2mqtt.yaml       # Локальная конфигурация (в git не хранится)
├── zigbee2mqtt.yaml.example
└── mosquitto/             # Конфигурация Mosquitto
    ├── Dockerfile
    ├── conf.d/
    │   ├── bridge.conf    # Локально (в git не хранится)
    │   └── bridge.conf.example
    └── etc/               # Локально (в docker volume)
```

## Команды CLI

```bash
# Статус
status, s          - Показать статус контейнеров
config, c          - Показать конфигурацию
devices, d         - Показать USB устройства
coordinator, coord - Определить тип координатора (ember/zstack) по USB
permit-join        - Переключить permit_join в zigbee2mqtt.yaml (on/off)
gen-configs        - Сгенерировать/восстановить конфиги из `.env` (templates/Jinja2)

# Управление
start              - Запустить сервисы
stop               - Остановить сервисы
restart            - Перезапустить сервисы
down               - Полная остановка
logs [service]     - Показать логи

# Настройка
set-device <path>  - Установить Zigbee устройство
set-mqtt-user <u>  - Установить MQTT пользователя
set-mqtt-pass <p>  - Установить MQTT пароль
enable-nodered     - Включить NodeRED
disable-nodered    - Выключить NodeRED
enable-cloud       - Включить облачный MQTT
disable-cloud      - Выключить облачный MQTT
```

## TUI интерфейс

TUI предоставляет интерактивный интерфейс с:
- Просмотром статуса контейнеров (автообновление каждые 5 сек)
- Редактированием настроек
- Выбором USB устройства из списка
- Просмотром логов
- Управлением сервисами

### Горячие клавиши

**Главный экран:**
- `s` - Запустить сервисы
- `x` - Остановить сервисы
- `r` - Перезапустить
- `l` - Открыть логи
- `c` - Настройки
- `q` / `Escape` - Выход

**Экран логов:**
- `1` - Логи MQTT
- `2` - Логи Zigbee2MQTT
- `3` - Логи NodeRED
- `0` - Все логи
- `r` - Обновить
- `f` - Переключить режим snapshot/follow (по умолчанию follow)


## Сборка .pyz архива

```bash
cd z2m_manager
./build.sh
```

Результат: `z2m.pyz` - автономный исполняемый архив.

## Решение проблем

### USB устройство не найдено

```bash
# Проверить подключение
lsusb | grep -i "cp210\|ch340\|cc2531\|conbee"

# Проверить права
ls -la /dev/ttyUSB* /dev/ttyACM*

# Если права crw-rw---- root dialout — добавить себя в группу:
sudo usermod -aG dialout $USER
# Перелогиниться!
```

### Docker не может открыть устройство

```bash
# Установить udev-правила
sudo cp 99-zigbee.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules

# Отключить и подключить адаптер
# Проверить симлинк
ls -la /dev/zigbee
```

### Ошибка "Permission denied" при запуске

```bash
# Проверить членство в группах
groups
# Должны быть: docker, dialout

# Если нет — добавить и перелогиниться
sudo usermod -aG docker,dialout $USER
```
