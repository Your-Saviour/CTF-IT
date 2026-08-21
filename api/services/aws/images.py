from collections.abc import Iterable, Mapping

from .compute import AwsComputeProvider, InstanceSpec
from .tags import assert_owned, aws_tag_dict, aws_tag_list
from .types import ImageResult, InstanceResult


class AwsImageProvider:
    """Owned EC2 AMI and EBS-snapshot lifecycle operations."""

    def __init__(self, ec2_client):
        self.ec2 = ec2_client
        self.compute = AwsComputeProvider(ec2_client)

    def _call(self, operation: str, **kwargs):
        return self.compute._call(operation, **kwargs)

    def wait_available(self, ami_id: str, *, delay: int = 10,
                       max_attempts: int = 120) -> None:
        self.ec2.get_waiter("image_available").wait(
            ImageIds=[ami_id], WaiterConfig={"Delay": delay, "MaxAttempts": max_attempts},
        )

    def create_image(self, instance_id: str, name: str,
                     tags: Mapping[str, str]) -> ImageResult:
        ami_id = self._call(
            "create_image", InstanceId=instance_id, Name=name, NoReboot=False,
            TagSpecifications=[{"ResourceType": "image", "Tags": aws_tag_list(tags)}],
        )["ImageId"]
        self.wait_available(ami_id)
        image = self._call("describe_images", ImageIds=[ami_id])["Images"][0]
        snapshots = tuple(
            row["Ebs"]["SnapshotId"] for row in image.get("BlockDeviceMappings", [])
            if row.get("Ebs", {}).get("SnapshotId")
        )
        self._call("create_tags", Resources=[ami_id, *snapshots], Tags=aws_tag_list(tags))
        return ImageResult(ami_id, snapshots, image.get("State", "unknown"))

    def ensure_image(self, instance_id: str, name: str,
                     tags: Mapping[str, str]) -> ImageResult:
        filters = [
            {"Name": "name", "Values": [name]},
            *({"Name": f"tag:{key}", "Values": [value]} for key, value in tags.items()),
        ]
        images = self._call("describe_images", Owners=["self"], Filters=filters).get(
            "Images", []
        )
        if len(images) > 1:
            raise RuntimeError("multiple AMIs match one owned image build")
        if not images:
            return self.create_image(instance_id, name, tags)
        image = images[0]
        assert_owned(aws_tag_dict(image.get("Tags")), tags)
        if image.get("State") != "available":
            self.wait_available(image["ImageId"])
            image = self._call("describe_images", ImageIds=[image["ImageId"]])["Images"][0]
        snapshots = tuple(
            row["Ebs"]["SnapshotId"] for row in image.get("BlockDeviceMappings", [])
            if row.get("Ebs", {}).get("SnapshotId")
        )
        self._call(
            "create_tags", Resources=[image["ImageId"], *snapshots], Tags=aws_tag_list(tags),
        )
        return ImageResult(image["ImageId"], snapshots, image.get("State", "unknown"))

    def launch_validation_instance(self, spec: InstanceSpec) -> InstanceResult:
        return self.compute.launch_instance(spec)

    def retire_owned(self, ami_id: str, snapshot_ids: Iterable[str],
                     expected_tags: Mapping[str, str]) -> None:
        image = self._call("describe_images", ImageIds=[ami_id])["Images"][0]
        assert_owned(aws_tag_dict(image.get("Tags")), expected_tags)
        self._call("deregister_image", ImageId=ami_id)
        for snapshot_id in snapshot_ids:
            snapshot = self._call("describe_snapshots", SnapshotIds=[snapshot_id])["Snapshots"][0]
            assert_owned(aws_tag_dict(snapshot.get("Tags")), expected_tags)
            self._call("delete_snapshot", SnapshotId=snapshot_id)
