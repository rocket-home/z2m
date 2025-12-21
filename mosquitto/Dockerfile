FROM eclipse-mosquitto:2.0.22

RUN sed -i 's/#password_file/password_file \/mosquitto\/etc\/password/g' /mosquitto/config/mosquitto.conf \
    && sed -i 's/#include_dir/include_dir \/mosquitto\/conf.d/g' /mosquitto/config/mosquitto.conf \
    && sed -i '/^listener/d' /mosquitto/config/mosquitto.conf \
    && sed -i '/^socket_domain/d' /mosquitto/config/mosquitto.conf \
    && echo "listener 1883 0.0.0.0" >> /mosquitto/config/mosquitto.conf \
    && echo "protocol mqtt" >> /mosquitto/config/mosquitto.conf \
    && echo "allow_anonymous true" >> /mosquitto/config/mosquitto.conf

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["/usr/sbin/mosquitto", "-c", "/mosquitto/config/mosquitto.conf"]
