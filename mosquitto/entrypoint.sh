#!/bin/sh
set -e

# Удаляем listener.conf если есть, чтобы избежать конфликтов (conf.d может быть read-only)
rm -f /mosquitto/conf.d/listener.conf 2>/dev/null || true

# В базовом mosquitto.conf у нас всегда указан password_file.
# Mosquitto не стартует, если файл не существует, даже когда allow_anonymous=true.
# Поэтому гарантируем, что файл есть (пустой допустим).
mkdir -p /mosquitto/etc 2>/dev/null || true
touch /mosquitto/etc/password 2>/dev/null || true
chown root:root /mosquitto/etc/password 2>/dev/null || true
chmod 600 /mosquitto/etc/password 2>/dev/null || true

# Проверяем, заданы ли переменные окружения для пользователя
if [ -n "$MQTT_USER" ] && [ -n "$MQTT_PASSWORD" ]; then
    # Проверяем, существует ли файл password и не пустой ли он
    if [ ! -f /mosquitto/etc/password ] || [ ! -s /mosquitto/etc/password ]; then
        # Создаем новый файл с пользователем (флаг -c создает новый файл)
        mosquitto_passwd -c -b /mosquitto/etc/password "$MQTT_USER" "$MQTT_PASSWORD"
        echo "Created new user: $MQTT_USER"
    else
        # Файл существует, добавляем/обновляем пользователя (без -c)
        mosquitto_passwd -b /mosquitto/etc/password "$MQTT_USER" "$MQTT_PASSWORD"
        echo "Added/updated user: $MQTT_USER"
    fi

    # Приводим права в порядок (без world-readable)
    # Важно: upstream ожидает owner=root (иначе future versions могут отказать).
    chown root:root /mosquitto/etc/password 2>/dev/null || true
    chmod 600 /mosquitto/etc/password 2>/dev/null || true
else
    echo "MQTT_USER and MQTT_PASSWORD not set, skipping user creation"
fi

# НЕ вызываем /docker-entrypoint.sh (он делает chown -R /mosquitto и ломается на read-only /mosquitto/conf.d).
# Вместо этого аккуратно выставляем права только на writable директории.
if [ "$(id -u)" = "0" ]; then
    for d in /mosquitto/data /mosquitto/etc /mosquitto/log; do
        [ -d "$d" ] && chown -R mosquitto:mosquitto "$d" 2>/dev/null || true
    done
fi

exec "$@"

