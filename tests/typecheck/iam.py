"""The IAM union variant must not shadow its enum payload."""

from typing import assert_type

from rboto_iam.types import PolicyIdentifier, PolicyIdentifierPolicyType


def policy_type(identifier: PolicyIdentifier) -> PolicyIdentifierPolicyType | None:
    if "policy_type" in identifier:
        return assert_type(identifier["policy_type"], PolicyIdentifierPolicyType)
    return None


policy_type({"policy_type": "user-managed"})
policy_type({"policy_arn": "arn:aws:iam::123456789012:policy/example"})
