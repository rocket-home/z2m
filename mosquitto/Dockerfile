FROM eclipse-mosquitto:2.0.11

RUN sed -i 's/#password_file/password_file mosquitto\/etc\/password/g' /mosquitto/config/mosquitto.conf \
    && sed -i 's/#socket_domain/socket_domain ipv4/g' /mosquitto/config/mosquitto.conf \
    && sed -i 's/#include_dir/include_dir \/mosquitto\/conf.d/g' /mosquitto/config/mosquitto.conf
