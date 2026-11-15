from rboto_dynamodb.types import AttributeValueOutput


def describe(value: AttributeValueOutput) -> str:
    if "s" in value:
        return value["s"]  # str
    if "n" in value:
        return f"number: {value['n']}"  # DynamoDB numbers are strings.
    if "unknown" in value:
        return "unrecognized variant"
    return "another known variant"
