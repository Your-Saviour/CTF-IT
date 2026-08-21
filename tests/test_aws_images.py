from api.services.aws.images import AwsImageProvider


class Waiter:
    def __init__(self, calls): self.calls = calls
    def wait(self, **kwargs): self.calls.append(("wait_image_available", kwargs))


class Ec2:
    def __init__(self): self.calls = []; self.existing_images = []
    def create_image(self, **kwargs): self.calls.append(("create_image", kwargs)); return {"ImageId": "ami-new"}
    def get_waiter(self, name): assert name == "image_available"; return Waiter(self.calls)
    def describe_images(self, **kwargs):
        self.calls.append(("describe_images", kwargs))
        if kwargs.get("Filters"):
            return {"Images": self.existing_images}
        return {"Images": [{"ImageId": "ami-new", "State": "available",
                            "Tags": [{"Key": "ManagedBy", "Value": "ctf-it"}],
                            "BlockDeviceMappings": [
                                {"Ebs": {"SnapshotId": "snap-root"}},
                                {"Ebs": {"SnapshotId": "snap-data"}},
                            ]}]}
    def create_tags(self, **kwargs): self.calls.append(("create_tags", kwargs)); return {}
    def deregister_image(self, **kwargs): self.calls.append(("deregister_image", kwargs)); return {}
    def describe_snapshots(self, **kwargs):
        self.calls.append(("describe_snapshots", kwargs))
        return {"Snapshots": [{"SnapshotId": kwargs["SnapshotIds"][0],
                               "Tags": [{"Key": "ManagedBy", "Value": "ctf-it"}]}]}
    def delete_snapshot(self, **kwargs): self.calls.append(("delete_snapshot", kwargs)); return {}


def test_create_image_records_and_tags_all_backing_snapshots():
    ec2 = Ec2()
    result = AwsImageProvider(ec2).create_image(
        "i-builder", "ctf-opnsense-26-7", {"ManagedBy": "ctf-it"},
    )
    assert result.ami_id == "ami-new"
    assert result.snapshot_ids == ("snap-root", "snap-data")
    assert ("create_tags", {"Resources": ["ami-new", "snap-root", "snap-data"],
                            "Tags": [{"Key": "ManagedBy", "Value": "ctf-it"}]}) in ec2.calls


def test_retire_deregisters_before_owned_snapshot_delete():
    ec2 = Ec2()
    AwsImageProvider(ec2).retire_owned(
        "ami-new", ["snap-root", "snap-data"], {"ManagedBy": "ctf-it"},
    )
    names = [name for name, _ in ec2.calls]
    assert names.index("deregister_image") < names.index("delete_snapshot")
    assert names.count("delete_snapshot") == 2


def test_ensure_image_reuses_owned_candidate_after_interrupted_create():
    ec2 = Ec2()
    ec2.existing_images = [{
        "ImageId": "ami-existing", "Name": "ctf-opnsense-26-7", "State": "available",
        "Tags": [{"Key": "ManagedBy", "Value": "ctf-it"}],
        "BlockDeviceMappings": [{"Ebs": {"SnapshotId": "snap-existing"}}],
    }]
    result = AwsImageProvider(ec2).ensure_image(
        "i-builder", "ctf-opnsense-26-7", {"ManagedBy": "ctf-it"},
    )
    assert result.ami_id == "ami-existing"
    assert result.snapshot_ids == ("snap-existing",)
    assert not any(name == "create_image" for name, _ in ec2.calls)
