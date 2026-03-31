FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV container=docker

# Core packages + systemd
RUN apt-get update && apt-get install -y --no-install-recommends \
    systemd \
    systemd-sysv \
    dbus \
    openssh-server \
    sudo \
    curl \
    python3 \
    net-tools \
    iproute2 \
    passwd \
    procps \
    iptables \
    ufw \
    vim \
    nano \
    wget \
    cron \
    rsyslog \
    man-db \
    ca-certificates \
    unzip \
    tar \
    gzip \
    git \
    findutils \
    apt-utils \
    lsof \
    tcpdump \
    nmap \
    file \
    less \
    whois \
    dnsutils \
    logrotate \
    python3-pip \
  && rm -rf /var/lib/apt/lists/*

# Mask systemd units that are unnecessary or broken in containers
RUN systemctl mask \
    systemd-resolved.service \
    systemd-networkd.service \
    systemd-networkd-wait-online.service \
    systemd-modules-load.service \
    systemd-remount-fs.service \
    systemd-timesyncd.service \
    systemd-udevd.service \
    sys-kernel-config.mount \
    sys-kernel-debug.mount \
    sys-kernel-tracing.mount \
    dev-hugepages.mount \
    getty@tty1.service \
    serial-getty@.service

# Configure journald for container use (volatile storage, capped size)
RUN mkdir -p /etc/systemd/journald.conf.d && \
    printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=16M\nForwardToConsole=yes\n' \
    > /etc/systemd/journald.conf.d/container.conf

# Enable SSH with root login allowed
RUN mkdir -p /run/sshd && \
    sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config && \
    systemctl enable ssh.service

# Set root password from build arg
ARG ROOT_PASSWORD
RUN echo "root:${ROOT_PASSWORD}" | chpasswd

STOPSIGNAL SIGRTMIN+3
VOLUME ["/run", "/run/lock", "/tmp"]
WORKDIR /root
CMD ["/lib/systemd/systemd"]
