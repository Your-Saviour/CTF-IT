class AwsProviderError(RuntimeError):
    """Base error for AWS provider operations."""


class AwsConfigurationError(AwsProviderError):
    """AWS configuration is absent or invalid."""


class AwsRetryableError(AwsProviderError):
    """AWS rejected an operation for a transient reason."""


class AwsTerminalError(AwsProviderError):
    """AWS rejected an operation that must not be retried unchanged."""


class AwsOwnershipError(AwsTerminalError):
    """A resource does not have the expected application ownership tags."""


class AwsQuotaError(AwsTerminalError):
    """An AWS account or regional quota is insufficient."""
