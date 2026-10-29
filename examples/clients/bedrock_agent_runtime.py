import asyncio

from rboto import BedrockAgentRuntimeClient
from rboto_bedrock_agent_runtime.types import KnowledgeBaseQuery


async def main() -> None:
    client = BedrockAgentRuntimeClient(region="us-east-1")
    query: KnowledgeBaseQuery = {"text": "How do I reset my password?"}
    response = await client.retrieve(knowledge_base_id="KB12345678", retrieval_query=query)
    for result in response.retrieval_results:
        print(result.content, result.score)


if __name__ == "__main__":
    asyncio.run(main())
