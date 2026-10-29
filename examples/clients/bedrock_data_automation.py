import asyncio

from rboto import BedrockDataAutomationClient


async def main() -> None:
    client = BedrockDataAutomationClient(region="us-east-1")
    response = await client.list_data_automation_projects(max_results=10)
    for project in response.projects:
        print(project.project_arn, project.project_name)


if __name__ == "__main__":
    asyncio.run(main())
