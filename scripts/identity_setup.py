"""Create the Cognito test user after `cdk deploy`.

The Gateway request interceptor forwards the caller's validated token to the
MCP target; there is no outbound OAuth provider or consent callback. This
script only creates the test user and prints the values the eval needs. It is
idempotent.
"""

import os
import sys

import boto3
from botocore.exceptions import ClientError

STACK = os.environ.get("STACK_NAME", "AgentCoreGatewayStack")


def stack_outputs() -> dict[str, str]:
    cf = boto3.client("cloudformation")
    stack = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    return {item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", [])}


def ensure_user(cognito, pool_id: str, username: str, password: str, email: str) -> None:
    try:
        cognito.admin_create_user(
            UserPoolId=pool_id,
            Username=username,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
            MessageAction="SUPPRESS",
        )
        print(f"created test user {username}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "UsernameExistsException":
            raise
        print(f"test user {username} already exists")
    cognito.admin_set_user_password(
        UserPoolId=pool_id, Username=username, Password=password, Permanent=True
    )


def main() -> int:
    password = os.environ.get("IDENTITY_TEST_PASSWORD", "")
    if not password:
        print("IDENTITY_TEST_PASSWORD is required", file=sys.stderr)
        return 2

    outputs = stack_outputs()
    pool_id = outputs["UserPoolId"]
    cognito = boto3.client("cognito-idp")

    username = os.environ.get("IDENTITY_TEST_USER", "support-user")
    ensure_user(cognito, pool_id, username, password, f"{username}@example.com")

    print()
    print(f"export COGNITO_USER_POOL_ID={pool_id}")
    print(f"export COGNITO_CLIENT_ID={outputs['ApiClientId']}")
    print(f"export IDENTITY_TEST_USER={username}")
    print("# IDENTITY_TEST_PASSWORD stays in your shell/LOCAL.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
