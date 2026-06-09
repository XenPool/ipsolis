FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends rsyslog \
 && rm -rf /var/lib/apt/lists/*
COPY rsyslog.conf /etc/rsyslog.conf
RUN mkdir -p /var/log/testlab
EXPOSE 5140/tcp
EXPOSE 5140/udp
# -n keeps rsyslog in the foreground; -f selects our config file.
CMD ["rsyslogd", "-n", "-f", "/etc/rsyslog.conf"]
