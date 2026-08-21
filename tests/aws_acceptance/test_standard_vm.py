def test_standard_vm_create_configure_destroy(aws_context):
    assert aws_context.account_id == aws_context.expected_account_id
    assert aws_context.tags["AcceptanceRunId"] == aws_context.run_id
    vm = aws_context.create_standard_vm()
    try:
        assert aws_context.run_standard_vm_smoke(vm) == "ctf-it-ready"
    finally:
        aws_context.destroy_standard_vm(vm)
    remaining = aws_context.inventory()
    assert vm["instance_id"] not in remaining["instances"]
    assert vm["allocation_id"] not in remaining["addresses"]
    assert vm["security_group_id"] not in remaining["security_groups"]
