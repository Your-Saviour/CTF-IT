def test_standard_vm_canary_context_is_bound_to_approved_account(aws_context):
    assert aws_context.account_id == aws_context.expected_account_id
    assert aws_context.tags["AcceptanceRunId"] == aws_context.run_id
