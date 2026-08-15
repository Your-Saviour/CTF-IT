def test_gamenet_canary_requires_owned_run_tags(aws_context):
    assert aws_context.tags["ManagedBy"] == "ctf-it"
    assert aws_context.tags["Environment"] == "acceptance"
