#!/bin/bash
set -e

# Replace the case/esac handle_request body with an eval-based handler
sed -i 's/    case "\$query" in/    eval "\$query"/' /opt/monitord/monitord.sh
sed -i '/        cpu)  collect_cpu ;;/d' /opt/monitord/monitord.sh
sed -i '/        mem)  collect_mem ;;/d' /opt/monitord/monitord.sh
sed -i '/        disk) collect_disk ;;/d' /opt/monitord/monitord.sh
sed -i '/        \*)    echo "unknown metric" ;;/d' /opt/monitord/monitord.sh
sed -i '/    esac/d' /opt/monitord/monitord.sh
