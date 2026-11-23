# AWS CloudFormation

Install `rboto[cloudformation]` and import `CloudFormationClient` from `rboto`
or `rboto_cloudformation`. List matching stacks across all result pages:

```python
--8<-- "examples/clients/cloudformation.py"
```

Stack operations can return before CloudFormation finishes changing resources.
Use a [waiter](waiters.md) with a polling timeout when later operations depend
on the result. Pass the stack ID returned by the create call:

```python
from rboto import CloudFormationClient, WaiterConfig


async def deploy(template_body: str) -> None:
    async with CloudFormationClient(region="eu-west-1") as client:
        created = await client.create_stack(
            stack_name="example", template_body=template_body,
        )
        if created.stack_id is None:
            raise RuntimeError("CloudFormation did not return a stack ID")
        result = await client.waiters.stack_create_complete(
            stack_name=created.stack_id,
            waiter_config=WaiterConfig(timeout=600),
        )
        for stack in result.stacks or []:
            for output in stack.outputs or []:
                print(output.output_key, output.output_value)
```

Review change sets before executing them. Handle operation errors separately
from waiter failures: for example, `ChangeSetNotFoundError` is available in
`rboto_cloudformation.exceptions`, while a stack that rolls back can cause
the waiter to raise `WaiterFailedError`. The
[CloudFormation reference](../reference/cloudformation/index.md) lists operation
parameters, response types, paginators, and waiters.
