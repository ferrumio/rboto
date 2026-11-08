from rboto import DynamoDBClient


async def run(endpoint_url: str, table_name: str, partition: str) -> list[str]:
    labels: list[str] = []
    async with DynamoDBClient(region="eu-west-2", endpoint_url=endpoint_url) as client:
        async for page in client.paginators.query(
            table_name=table_name,
            key_condition_expression="#pk = :pk",
            expression_attribute_names={"#pk": "pk"},
            expression_attribute_values={":pk": {"s": partition}},
        ):
            for item in page.items or []:
                label = item.get("label")
                value = label.get("s") if label is not None else None
                if isinstance(value, str):
                    labels.append(value)
    return labels
