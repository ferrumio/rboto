from typing import assert_type

from rboto import (
    EventBridgePipesClient,
    EventBridgeSchedulerClient,
    S3Client,
    Session,
    SESV2Client,
    StepFunctionsClient,
    s3,
)
from rboto_pipes import PipesClient
from rboto_s3 import S3Client as ServiceS3Client
from rboto_scheduler import SchedulerClient
from rboto_sesv2 import SESv2Client
from rboto_sfn import SFNClient


def public_clients(session: Session) -> None:
    assert_type(S3Client(session=session), ServiceS3Client)
    assert_type(s3(session=session), S3Client)
    assert_type(StepFunctionsClient(session=session), SFNClient)
    assert_type(SESV2Client(session=session), SESv2Client)
    assert_type(EventBridgePipesClient(session=session), PipesClient)
    assert_type(EventBridgeSchedulerClient(session=session), SchedulerClient)
