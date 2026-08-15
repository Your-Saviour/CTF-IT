import boto3

from .config import AwsConfig
from .types import AwsIdentity


class AwsSessionFactory:
    def __init__(self, config: AwsConfig):
        self.config = config
        self._session = boto3.Session(
            profile_name=config.profile,
            region_name=config.default_region,
        )

    def client(self, service: str, region: str | None = None):
        return self._session.client(
            service,
            region_name=region or self.config.default_region,
        )

    def caller_identity(self) -> AwsIdentity:
        payload = self.client("sts").get_caller_identity()
        return AwsIdentity(
            account_id=payload["Account"],
            arn=payload["Arn"],
            user_id=payload["UserId"],
        )
