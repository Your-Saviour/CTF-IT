from unittest.mock import MagicMock, patch

import paramiko
import pytest

from api.services.ssh_connection import connect_vm


def _vm(stored_key=None):
    vm = MagicMock()
    vm.ip_address = "192.0.2.10"
    vm.hostname = "target-1"
    vm.ssh_port = 22
    vm.ssh_user = "root"
    vm.ssh_host_key = stored_key
    return vm


@patch("api.services.ssh_connection.paramiko.SSHClient")
@patch("api.services.ssh_connection.paramiko.Ed25519Key.from_private_key")
@patch("api.services.ssh_connection.get_or_create_platform_keypair", return_value=("key", "pub"))
@patch("api.services.ssh_connection.read_remote_host_key", return_value="ssh-ed25519 AAAA")
def test_first_connection_pins_host_key(_read, _keys, _pkey, client_type):
    vm = _vm()
    db = MagicMock()
    connect_vm(vm, db)
    assert vm.ssh_host_key == "ssh-ed25519 AAAA"
    db.commit.assert_called_once()
    client_type.return_value.connect.assert_called_once()


@patch("api.services.ssh_connection.read_remote_host_key", return_value="ssh-ed25519 CHANGED")
def test_changed_host_key_is_rejected(_read):
    vm = _vm("ssh-ed25519 ORIGINAL")
    with pytest.raises(paramiko.SSHException, match="host key changed"):
        connect_vm(vm, MagicMock())
