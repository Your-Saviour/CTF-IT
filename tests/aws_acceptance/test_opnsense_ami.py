import json


def test_opnsense_canary_builds_validates_and_activates_ami(aws_context, aws_opnsense_image):
    image = aws_opnsense_image.image
    assert image.region == aws_context.region
    assert image.status == "active"
    assert image.ami_id.startswith("ami-")
    evidence = json.loads(image.validation_results)
    assert evidence["public_clone"]["passed"] is True
    assert evidence["private_clone"]["passed"] is True
    assert evidence["public_clone"]["ssh_host_key"] != evidence["private_clone"]["ssh_host_key"]
