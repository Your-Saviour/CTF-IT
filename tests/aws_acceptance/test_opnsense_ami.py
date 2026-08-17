import json
import os


def test_opnsense_canary_builds_validates_and_activates_ami(aws_context, aws_opnsense_image):
    image = aws_opnsense_image.image
    assert image.region == aws_context.region
    assert image.status == "active"
    assert image.ami_id.startswith("ami-")
    evidence = json.loads(image.validation_results)
    if aws_opnsense_image.cache_hit:
        assert evidence["cache_hit"]["passed"] is True
        assert aws_opnsense_image.cached_ami.ami_id == image.ami_id
    else:
        assert evidence["public_clone"]["passed"] is True
        assert evidence["private_clone"]["passed"] is True
        assert evidence["public_clone"]["ssh_host_key"] != evidence["private_clone"]["ssh_host_key"]
    if os.environ.get("AWS_ACCEPTANCE_FORCE_OPNSENSE_BUILD") == "1":
        assert aws_opnsense_image.forced_build is True
        assert aws_opnsense_image.cache_hit is False
