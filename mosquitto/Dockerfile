FROM eclipse-mosquitto:2.0.10

RUN sed -i 's/#socket_domain/socket_domain ipv4/g' /mosquitto/config/mosquitto.conf \
    && sed -i 's/#include_dir/include_dir \/mosquitto\/conf.d/g' /mosquitto/config/mosquitto.conf
