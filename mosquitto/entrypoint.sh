#!/bin/sh
set -e

# Удаляем listener.conf если есть, чтобы избежать конфликтов
rm -f /mosquitto/conf.d/listener.conf

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
    chown mosquitto:mosquitto /mosquitto/etc/password 2>/dev/null || true
    chmod 600 /mosquitto/etc/password 2>/dev/null || true
else
    echo "MQTT_USER and MQTT_PASSWORD not set, skipping user creation"
fi

# Запускаем оригинальный entrypoint mosquitto
exec /docker-entrypoint.sh "$@"

